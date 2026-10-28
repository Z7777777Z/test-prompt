"""
大模型调用视图（2.0 功能完善 · 真实调用）
------------------------------------------------------------
原来的「调试」是前端模拟：单次调试返回一段写死的示意文本，
批量调试按问题文本哈希伪随机地"猜"对错。本模块把这两件事换成真实调用：

  POST /api/llm/chat/    单次调试：系统提示词 +（多轮）用户提示词 → 模型真实回答
  POST /api/llm/batch/   批量评估：一批「问题 + 标准答案」→ 逐条用**评估对象模型**生成
                         「模型答案」→ 再由**独立的判定模型**（配置见 config/judge_config.json）
                         判定「标准答案 vs 模型答案」是否一致 → 返回每条结果

设计要点：
  · 前端分批（默认每批 5 条）调用 batch，批次内后端并发，
    这样"上百条用例"既能跑得动，进度也能小步回传、可见可中断。
  · 单条失败不影响整批：失败信息回填该条 error，其余照常返回。
  · 生成答案与判定用两个模型：判定模型配置每次请求读一次并复用；
    判定失败只把该条留为「待判定」（judge_error 回传原因），不影响答案本身。
  · 计费不在此计算 —— 返回真实 token 数，由前端按模型单价折算（口径与页面一致）；
    其中 in/out 只统计**评估对象模型**的用量，判定模型的用量单列 judge_in/judge_out。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import db, judge_config, llm


def _json_body(request):
    """解析 POST 的 JSON 体；失败返回 (None, 错误响应)。"""
    try:
        return json.loads(request.body.decode("utf-8") or "{}"), None
    except ValueError:
        return None, JsonResponse({"ok": False, "error": "请求体不是合法 JSON"}, status=400)


def _call_info(data):
    """从请求体解析出模型调用信息；缺失返回 (None, 错误响应)。"""
    info = db.get_model_call_info(data.get("model_id"))
    if not info:
        return None, JsonResponse(
            {"ok": False, "error": "模型不存在或未选择模型，请重新选择模型"}, status=400)
    return info, None


# ------------------------------------------------------------
# 单次调试
# ------------------------------------------------------------
@csrf_exempt
def llm_chat(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    data, err = _json_body(request)
    if err:
        return err
    info, err = _call_info(data)
    if err:
        return err

    sys_prompt = data.get("sys_prompt") or ""
    user_prompts = data.get("user_prompts")
    if user_prompts is None or isinstance(user_prompts, str):
        user_prompts = [user_prompts or ""]

    messages = llm.build_messages(sys_prompt, user_prompts)
    if not messages:
        return JsonResponse({"ok": False, "error": "请输入系统提示词或用户提示词"}, status=400)

    try:
        content, usage, latency, _raw = llm.chat(
            info.get("base_url"), data.get("api_key") or "", info.get("model_name"),
            messages, api_url=info.get("api_url") or "",
            temperature=data.get("temperature"), max_tokens=data.get("max_tokens"),
        )
    except llm.LLMError as e:
        # 502：上游模型/网关错误，原因原样回传，前端直接 toast
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    return JsonResponse({
        "ok": True,
        "content": content,
        "usage": usage,
        "latency_ms": latency,
        "model": info.get("model_name"),
        "platform": info.get("platform"),
        "endpoint": llm.endpoint(info.get("base_url"), info.get("api_url") or ""),
    })


# ------------------------------------------------------------
# 批量调试
# ------------------------------------------------------------
@csrf_exempt
def llm_batch(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    data, err = _json_body(request)
    if err:
        return err
    info, err = _call_info(data)
    if err:
        return err

    cases = data.get("cases") or []
    if not isinstance(cases, list) or not cases:
        return JsonResponse({"ok": False, "error": "没有可调试的用例"}, status=400)

    sys_prompt = data.get("sys_prompt") or ""
    api_key = data.get("api_key") or ""
    judge_it = bool(data.get("judge", True))       # 默认：模型自动判定
    temperature = data.get("temperature")
    max_tokens = data.get("max_tokens")

    # 判定模型配置：整批读一次并复用（评估对象模型 ≠ 判定模型）
    jcfg = judge_config.load() if judge_it else None

    try:
        workers = int(data.get("concurrency") or 5)
    except (TypeError, ValueError):
        workers = 5
    workers = max(1, min(workers, 10))

    def run(case):
        c = case or {}
        q = (c.get("question") or "").strip()
        a = c.get("answer") or ""
        if not q:
            return {"question": c.get("question") or "", "m_answer": "", "is_true": None,
                    "in_tokens": 0, "out_tokens": 0, "latency_ms": 0,
                    "error": "问题为空，已跳过", "judge_error": None}
        return llm.answer_one(
            info, api_key, sys_prompt, q, a,
            judge_it=judge_it, temperature=temperature, max_tokens=max_tokens,
            judge_cfg=jcfg,
        )

    results = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, c): i for i, c in enumerate(cases)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:                     # noqa: BLE001
                results[i] = {
                    "question": (cases[i] or {}).get("question") or "",
                    "m_answer": "", "is_true": None,
                    "in_tokens": 0, "out_tokens": 0, "latency_ms": 0,
                    "error": "内部错误：%s" % (e,), "judge_error": None,
                }

    ok_count = sum(1 for r in results if r and not r.get("error"))
    return JsonResponse({
        "ok": True,
        "results": results,
        "ok_count": ok_count,
        "total": len(results),
        "model": info.get("model_name"),
        # 判定模型信息（不含 api_key），便于前端/排查确认判定走的是哪个模型
        "judge_model": (jcfg or {}).get("model") or "",
        "judge_base_url": (jcfg or {}).get("base_url") or "",
    })
