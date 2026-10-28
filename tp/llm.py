"""
大模型调用层 · OpenAI 兼容 Chat Completions
------------------------------------------------------------
设计口径：
  1. 只依赖 Python 标准库（urllib），不引入任何第三方依赖，
     避免给「魔搭创空间」镜像再添构建风险。
  2. 调用地址：优先取 tp_model.api_url（模型级接口），
     为空则回退到所属服务器的 base_url + '/chat/completions'
     —— 与页面里「选择模型」弹窗的提示文案口径一致。
  3. 返回值统一，屏蔽各家差异：
       content    文本答案（兼容 string / 多段 list / text 字段）
       usage      {in, out, total} —— 取不到时全 0，由前端回落估算
  4. 判定（judge）：用**独立的判定模型**当裁判（配置见 config/judge_config.json，
     由 tp/judge_config.py 读取），输出「正确 / 错误」，
     解析成 1 / 0 / None（None = 未判定）。
     —— 判定模型与「被评估的模型」解耦：后者只负责生成模型答案。
"""
import json
import time
import urllib.error
import urllib.request

from . import judge_config

# 单次调用超时（秒）：推理模型（R1 / o 系列）较慢，留足余量
DEFAULT_TIMEOUT = 120

# 判定用的系统提示词：只要求输出一个词，便于稳定解析
JUDGE_SYSTEM = (
    "你是严格、公正的答案评审员。请判断【模型答案】与【标准答案】表达的含义是否一致。"
    "判定口径："
    "① 文字类：措辞、语序、详略可以不同，只要关键事实、数值、结论一致，即算一致；"
    "② 代码类：实现方式、变量名、注释、写法可以不同，只要功能等价、输出结果一致，即算一致；"
    "③ 模型答案缺失关键信息、答非所问，或与标准答案矛盾（结论相反、数值不同、功能不等价），判为不一致。"
    "只输出一个词：正确 或 错误。不要输出任何其它内容。"
)


class LLMError(Exception):
    """调用失败：网络 / 鉴权 / 协议 / 服务端错误。"""


# ------------------------------------------------------------
# 地址与消息组装
# ------------------------------------------------------------
def endpoint(base_url: str, api_url: str = "") -> str:
    """模型级 api_url 优先；否则 server.base_url + /chat/completions。"""
    url = (api_url or "").strip()
    if url:
        return url
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("缺少接口地址：请为该模型或其所属服务器配置 base_url / api_url")
    # 用户可能已经把 /chat/completions 写进了 base_url
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def build_messages(sys_prompt: str, user_prompts) -> list:
    """系统提示词 + 用户提示词（数组，支持多轮）→ OpenAI messages。"""
    messages = []
    if (sys_prompt or "").strip() != "":
        messages.append({"role": "system", "content": sys_prompt})
    if isinstance(user_prompts, str):
        user_prompts = [user_prompts]
    for p in (user_prompts or []):
        if p is None:
            continue
        messages.append({"role": "user", "content": str(p)})
    return messages


# ------------------------------------------------------------
# 响应解析
# ------------------------------------------------------------
def _extract_content(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        # 少数网关把内容放在非标准位置
        msg = data.get("message") or {}
        return str(msg.get("content") or data.get("output_text") or "").strip()

    ch = choices[0] or {}
    msg = ch.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = ch.get("text")          # /completions 风格
    if isinstance(content, list):         # 多段内容（部分多模态返回）
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
            else:
                parts.append(str(p))
        content = "".join(parts)
    return str(content or "").strip()


def _extract_usage(data: dict) -> dict:
    u = data.get("usage") or {}
    i = u.get("prompt_tokens")
    o = u.get("completion_tokens")
    if i is None:
        i = u.get("input_tokens")         # 兼容部分网关命名
    if o is None:
        o = u.get("output_tokens")
    try:
        i = int(i or 0)
        o = int(o or 0)
    except (TypeError, ValueError):
        i = o = 0
    t = u.get("total_tokens")
    try:
        t = int(t) if t else i + o
    except (TypeError, ValueError):
        t = i + o
    return {"in": i, "out": o, "total": t}


def parse_verdict(text: str):
    """把裁判输出解析成 1 / 0 / None。取「正确 / 错误」中最先出现的那个。"""
    t = (text or "").strip()
    i_ok = t.find("正确")
    i_bad = t.find("错误")
    if i_ok == -1 and i_bad == -1:
        return None
    if i_ok == -1:
        return 0
    if i_bad == -1:
        return 1
    return 1 if i_ok < i_bad else 0


# ------------------------------------------------------------
# 调用
# ------------------------------------------------------------
def chat(base_url, api_key, model_name, messages, api_url="",
         temperature=None, max_tokens=None, timeout=DEFAULT_TIMEOUT):
    """调用一次对话补全。

    返回 (content, usage, latency_ms, raw_text)
    失败抛 LLMError（带可读原因，前端直接 toast 给用户）。
    """
    url = endpoint(base_url, api_url)

    payload = {"model": model_name, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if (api_key or "").strip():
        req.add_header("Authorization", "Bearer " + api_key.strip())

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        hint = {
            401: "api_key 无效或已过期",
            403: "无权访问该模型（也可能是地区/余额限制）",
            404: "接口地址或模型名不正确",
            429: "触发限流或余额不足，请稍后重试",
        }.get(e.code, e.reason)
        raise LLMError("HTTP %s · %s%s" % (e.code, hint, ("｜" + detail) if detail else ""))
    except urllib.error.URLError as e:
        raise LLMError("网络错误：%s（请检查 base_url 是否可达）" % (e.reason,))
    except Exception as e:                                   # noqa: BLE001
        raise LLMError("调用异常：%s" % (e,))
    latency = int((time.time() - t0) * 1000)

    try:
        data = json.loads(raw)
    except ValueError:
        raise LLMError("响应不是合法 JSON：%s" % raw[:200])

    return _extract_content(data), _extract_usage(data), latency, raw


def judge(question, answer, m_answer, config=None, timeout=None):
    """让「判定模型」当裁判，返回 (1/0/None, usage, latency_ms)。

    判定模型来自 config/judge_config.json（api_key 也只在那里，不写死进代码）；
    传入 config 时复用调用方已读到的配置，避免逐条重复读文件。
    """
    cfg = config or judge_config.load()

    base_url = (cfg.get("base_url") or "").strip()
    model_name = (cfg.get("model") or "").strip()
    if not base_url:
        raise LLMError("未配置判定模型基址，请检查 config/judge_config.json 的 base_url")
    if not model_name:
        raise LLMError("未配置判定模型名称，请检查 config/judge_config.json 的 model")

    user = (
        "问题：%s\n\n标准答案：%s\n\n模型答案：%s\n\n请只回复：正确 或 错误"
        % (question or "", answer or "", m_answer or "")
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
    content, usage, latency, _raw = chat(
        base_url, cfg.get("api_key") or "", model_name, messages,
        api_url=cfg.get("api_url") or "", temperature=0,
        timeout=timeout or cfg.get("timeout") or DEFAULT_TIMEOUT,
    )
    return parse_verdict(content), usage, latency


def answer_one(call_info, api_key, sys_prompt, question, answer="", judge_it=True,
               temperature=None, max_tokens=None, timeout=DEFAULT_TIMEOUT,
               judge_cfg=None):
    """批量评估的单条流程：先用「评估对象模型」要答案，再（可选）用「判定模型」判对错。

    call_info : db.get_model_call_info() 的返回值（评估对象模型：base_url / api_url / model_name）
    judge_cfg : 判定模型配置（tp/judge_config.py::load() 的结果）；不传则内部自行读取

    返回 dict：
      question / m_answer（模型答案，评估对象模型输出）/ is_true（1 正确 / 0 错误 / None 待判定）
      in_tokens / out_tokens        —— 评估对象模型的真实用量（用于页面计费）
      judge_in_tokens / judge_out_tokens —— 判定模型的用量（不并入上面的计费口径）
      latency_ms / error（生成答案失败）/ judge_error（判定失败原因）
    """
    out = {
        "question": question,
        "m_answer": "",
        "is_true": None,
        "in_tokens": 0, "out_tokens": 0,
        "judge_in_tokens": 0, "judge_out_tokens": 0,
        "latency_ms": 0,
        "error": None,
        "judge_error": None,
    }
    try:
        messages = build_messages(sys_prompt, [question])
        content, usage, latency, _raw = chat(
            call_info.get("base_url"), api_key, call_info.get("model_name"),
            messages, api_url=call_info.get("api_url") or "",
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )
        out["m_answer"] = content
        out["in_tokens"] = usage["in"]
        out["out_tokens"] = usage["out"]
        out["latency_ms"] = latency
        if not content:
            out["error"] = "模型返回了空内容"
    except LLMError as e:
        out["error"] = str(e)
        return out

    # 标准答案为空时无从判定，保持「待判定」
    if judge_it and (answer or "").strip() != "":
        try:
            verdict, jusage, jlatency = judge(
                question, answer, out["m_answer"],
                config=judge_cfg, timeout=timeout,
            )
            out["is_true"] = verdict
            out["judge_in_tokens"] = jusage["in"]
            out["judge_out_tokens"] = jusage["out"]
            out["latency_ms"] += jlatency
            if verdict is None:
                out["judge_error"] = "判定模型未返回可识别的结论（正确 / 错误）"
        except LLMError as e:
            # 判定失败不影响答案本身，留待人工判定
            out["is_true"] = None
            out["judge_error"] = str(e)
    return out
