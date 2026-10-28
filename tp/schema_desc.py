"""「表结构」页 · 生成表结构说明时，用 AI 补充数据库里没有的表/字段备注。

背景
----
各数据库对「注释」的支持并不一致：MySQL 有 COLUMN_COMMENT、PostgreSQL 有
pg_description，而 **SQLite 根本不存注释**（连表注释也没有）。因此从真实库内省
出来的表结构，备注列经常是空的。

口径（按需求）
--------------
· 只补充「库里没有备注」的表说明与字段备注；库里已有的一律原样保留，不改写。
· 只描述该表/字段**实际存储的内容**（数据字典口径），不臆造业务含义。
· 模型走硅基流动（base_url / model / api_key 读 config/settings.py，可用环境变量覆盖）。
· 表关系（describe_relations()）：**真实外键优先，AI 兜底**——库里已建外键的关系必定保留，
  只有当某些表既不被引用也不引用别人（或库里根本没建外键）时，才让 AI 按命名与业务含义补充。

调用协议：OpenAI 兼容 /chat/completions，复用 tp/llm.py 的 chat()（纯标准库）。

数据结构（与 sql_conn.py / 页面完全一致）：
  tables = [{"name": 表名, "cn": 表说明,
             "cols": [[字段名, 类型, 是否必填(1/0), 键(PK/UK/FK/""), 默认值, 注释], ...]}]
本模块只在「原值为空」的位置写入 AI 结果，顺序与长度保持不变，便于前端按下标回填。
"""
import json

from . import llm

# 每批最多送多少张表给模型：表太多时拆批，避免单次请求过长、返回难以解析
BATCH_MAX_TABLES = 8

DESCRIBE_SYSTEM = (
    "你是资深数据库设计专家，负责把数据库的表结构整理成中文数据字典（表说明 + 字段备注）。"
    "要求："
    "① 只描述该表 / 该字段**实际存储的内容**和业务用途，表说明 6~18 个字，字段备注 2~14 个字；"
    "② 严格依据给出的表名、字段名、数据类型、必填/主键等信息，以及「数据库用途描述」来推断，"
    "不要臆造业务上不存在的含义；"
    "③ 只补充「没有备注」的表和字段；已经给了备注的，原样保留，不要改写；"
    "④ 只输出 JSON，不要输出解释、Markdown 代码块标记或任何多余文字。"
)

# 表关系（真实外键为空 / 覆盖不全时，由 AI 按命名与业务含义兜底推断）
RELATION_SYSTEM = (
    "你是资深数据库设计专家，负责梳理数据库表与表之间的关联关系。"
    "要求："
    "① 只输出确实成立的关系：真实外键、命名上明确指向某表主键的字段"
    "（如 xxx_id 指向 xxx.id、xxx_code 指向 xxx.code），或由业务含义可以确定的从属 / 引用关系；"
    "② 只能使用给出的表名与字段名，**不得编造不存在的表或字段**；"
    "③ 已给出的「已知外键」必须原样保留在结果里，不得删除或改写；"
    "④ 关系方向统一为「引用方 → 被引用方」，type 取 1:1 / N:1 / 1:N / N:N（从引用方视角）；"
    "⑤ 只输出 JSON，不要输出解释、Markdown 代码块标记或任何多余文字。"
)


def ai_config():
    """读取硅基流动配置：settings.py 为默认，环境变量覆盖。"""
    try:
        from django.conf import settings  # 延迟导入：脱离 Django 也能单独使用
        base_url = getattr(settings, "SILICONFLOW_BASE_URL", "")
        model = getattr(settings, "SILICONFLOW_MODEL", "")
        api_key = getattr(settings, "SILICONFLOW_API_KEY", "")
        timeout = getattr(settings, "SILICONFLOW_TIMEOUT", 120)
    except Exception:                                   # noqa: BLE001 —— 非 Django 环境兜底
        import os
        base_url = os.environ.get("TP_SF_BASE_URL", "https://api.siliconflow.cn/v1")
        model = os.environ.get("TP_SF_MODEL", "deepseek-ai/DeepSeek-V4-Pro")
        api_key = os.environ.get("TP_SF_API_KEY", "")
        timeout = os.environ.get("TP_SF_TIMEOUT", 120)

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 120
    return {
        "base_url": str(base_url or "").strip(),
        "model": str(model or "").strip(),
        "api_key": str(api_key or "").strip(),
        "timeout": timeout,
    }


# ------------------------------------------------------------
# 提示词组装
# ------------------------------------------------------------
def _brief_table(t: dict) -> dict:
    """把一张表压成送模型的最小结构（备注为空串也要带上，便于按名对齐）。"""
    cols = []
    for c in (t.get("cols") or []):
        cols.append({
            "name": str(c[0] if len(c) > 0 else ""),
            "type": str(c[1] if len(c) > 1 else ""),
            "required": bool(c[2]) if len(c) > 2 else False,
            "key": str(c[3] if len(c) > 3 else ""),
            "default": str(c[4] if len(c) > 4 else ""),
            "comment": str(c[5] if len(c) > 5 else ""),
        })
    return {"name": str(t.get("name") or ""), "cn": str(t.get("cn") or ""), "cols": cols}


def build_user_prompt(db_desc: str, tables) -> str:
    desc = (db_desc or "").strip() or "（未提供）"
    payload = [_brief_table(t) for t in tables]
    return (
        "数据库用途描述：%s\n\n"
        "表清单（JSON）：\n%s\n\n"
        "请为上面的表补充中文表说明（cn），并为其中 comment 为空的字段补充中文备注；"
        "已有 comment 的字段保持不变。"
        "严格按下面结构输出 JSON（表名与字段名必须与输入完全一致，不要增删表或字段）：\n"
        '{"tables":[{"name":"表名","cn":"表说明","cols":[{"name":"字段名","comment":"字段备注"}]}]}'
        % (desc, json.dumps(payload, ensure_ascii=False))
    )


# ------------------------------------------------------------
# 响应解析
# ------------------------------------------------------------
def _strip_fence(text: str) -> str:
    """去掉模型可能加上的 ```json ... ``` 包裹，取其中最外层 JSON 对象。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    return s.strip()


def parse_desc(text: str):
    """解析模型输出 → {表名: {"cn": str, "cols": {字段名: 备注}}}；解析失败返回 None。"""
    raw = _strip_fence(text)
    try:
        data = json.loads(raw)
    except ValueError:
        return None

    out = {}
    for t in (data.get("tables") or []):
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        cols = {}
        for c in (t.get("cols") or []):
            if isinstance(c, dict):
                cn = str(c.get("name") or "").strip()
                if cn:
                    cols[cn] = str(c.get("comment") or "").strip()
        out[name] = {"cn": str(t.get("cn") or "").strip(), "cols": cols}
    return out


def _merge(tables, parsed) -> int:
    """把模型结果写回**原先为空**的位置，返回补充的处数（表说明 + 字段备注）。"""
    filled = 0
    for t in tables:
        got = parsed.get(t.get("name"))
        if not got:
            continue
        if not (t.get("cn") or "").strip() and got["cn"]:
            t["cn"] = got["cn"]
            filled += 1
        for c in (t.get("cols") or []):
            if len(c) > 5 and not (c[5] or "").strip():
                val = got["cols"].get(str(c[0]))
                if val:
                    c[5] = val
                    filled += 1
    return filled


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ------------------------------------------------------------
# 对外入口
# ------------------------------------------------------------
def describe(db_desc: str, tables, config=None, timeout=None):
    """为缺失的表/字段备注做 AI 补充（就地修改 tables 并返回统计）。

    返回 dict：
      tables          —— 传入的同一份数据结构（已就地补充）
      filled          —— 补充的处数（表说明 + 字段备注）
      filled_tables   —— 补充了表说明的表数
      usage_in/out    —— 硅基流动返回的真实用量
      latency_ms      —— 累计耗时
      errors          —— 分批失败的原因列表（成功批次不受影响）
    全部批次都失败时抛 llm.LLMError，把首个原因交给上层展示。
    """
    tables = tables or []
    if not tables:
        return {"tables": tables, "filled": 0, "filled_tables": 0,
                "usage_in": 0, "usage_out": 0, "latency_ms": 0, "errors": []}

    cfg = config or ai_config()
    if not cfg["base_url"] or not cfg["model"]:
        raise llm.LLMError("未配置硅基流动基址或模型（请检查 config/settings.py）")

    usage_in = usage_out = latency = 0
    filled = 0
    errors = []
    ok_batches = 0

    for batch in _chunks(list(tables), BATCH_MAX_TABLES):
        messages = [
            {"role": "system", "content": DESCRIBE_SYSTEM},
            {"role": "user", "content": build_user_prompt(db_desc, batch)},
        ]
        try:
            content, usage, ms, _raw = llm.chat(
                cfg["base_url"], cfg["api_key"], cfg["model"], messages,
                temperature=0, timeout=timeout or cfg["timeout"],
            )
        except llm.LLMError as e:
            errors.append(str(e))
            continue

        parsed = parse_desc(content)
        if parsed is None:
            errors.append("模型未返回可解析的 JSON 表结构说明")
            continue

        filled += _merge(batch, parsed)
        ok_batches += 1
        usage_in += usage["in"]
        usage_out += usage["out"]
        latency += ms

    if ok_batches == 0:
        # 一批都没成功：把原因抛给上层（前端提示后保留原始备注，不影响已提取的结构）
        raise llm.LLMError(errors[0] if errors else "AI 补充备注失败")

    return {
        "tables": tables,
        "filled": filled,
        "usage_in": usage_in, "usage_out": usage_out, "latency_ms": latency,
        "errors": errors,
    }


# ------------------------------------------------------------
# 表关系：真实外键优先，覆盖不全时由 AI 兜底推断
# ------------------------------------------------------------
def scoped_rels(tables, raw_rels):
    """把真实外键收敛到「引用方在本批表内」的范围，并规范化字段名。

    引用方不在选中表内的一律丢弃；被引用表可以不在这批里（关系仍属于当前表）。
    """
    names = {str(t.get("name") or "") for t in (tables or [])}
    out = []
    seen = set()
    for r in (raw_rels or []):
        if not isinstance(r, dict):
            continue
        f = str(r.get("from") or "").strip()
        to = str(r.get("to") or "").strip()
        if not f or not to or f not in names:
            continue
        item = {
            "from": f,
            "from_col": str(r.get("from_col") or "").strip(),
            "to": to,
            "to_col": str(r.get("to_col") or "").strip(),
        }
        k = (item["from"], item["from_col"], item["to"], item["to_col"])
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def need_ai_rels(tables, known_rels) -> bool:
    """是否需要 AI 兜底：库里没有任何真实外键，或有表既不被引用也不引用别人。"""
    if not known_rels:
        return True
    touched = set()
    for r in known_rels:
        touched.add(r["from"])
        touched.add(r["to"])
    return any(str(t.get("name") or "") not in touched for t in (tables or []))


def _brief_table_rel(t: dict) -> dict:
    """关系推断只需「表名 / 表说明 / 主键 / 字段名+类型」，不带默认值与备注，省 token。"""
    pk = []
    cols = []
    for c in (t.get("cols") or []):
        name = str(c[0] if len(c) > 0 else "")
        if not name:
            continue
        if len(c) > 3 and str(c[3] or "").upper() == "PK":
            pk.append(name)
        cols.append({"name": name, "type": str(c[1] if len(c) > 1 else "")})
    return {"name": str(t.get("name") or ""), "cn": str(t.get("cn") or ""),
            "pk": pk, "cols": cols}


def build_relation_prompt(db_desc: str, tables, known_rels) -> str:
    desc = (db_desc or "").strip() or "（未提供）"
    payload = [_brief_table_rel(t) for t in tables]
    return (
        "数据库用途描述：%s\n\n"
        "表清单（JSON）：\n%s\n\n"
        "已知外键（必须保留在结果中）：\n%s\n\n"
        "请梳理上面这些表之间的关联关系，补充**已知外键之外**确实存在的关系；"
        "只允许使用上面出现过的表名与字段名，表名或字段名对不上的一律不要输出。\n"
        "严格按下面结构输出 JSON：\n"
        '{"relations":[{"from":"引用方表名","from_col":"引用方字段",'
        '"to":"被引用表名","to_col":"被引用字段","type":"N:1","desc":"关系说明"}]}\n'
        "没有任何关系时输出 {\"relations\":[]}。"
        % (desc, json.dumps(payload, ensure_ascii=False),
           json.dumps(known_rels or [], ensure_ascii=False))
    )


def parse_relations(text: str):
    """解析模型输出 → [{from, from_col, to, to_col, type, desc}]；解析失败返回 None。

    必须显式给出 relations 键（可以是空数组）：模型答非所问（例如又返回一份表结构）
    按「解析失败」处理，好让上层能把真实外键原样保留并给出可读提示。
    """
    raw = _strip_fence(text)
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or "relations" not in data:
        return None

    out = []
    for r in (data.get("relations") or []):
        if not isinstance(r, dict):
            continue
        frm = str(r.get("from") or "").strip()
        to = str(r.get("to") or "").strip()
        if not frm or not to:
            continue
        out.append({
            "from": frm,
            "from_col": str(r.get("from_col") or "").strip(),
            "to": to,
            "to_col": str(r.get("to_col") or "").strip(),
            "type": str(r.get("type") or "").strip(),
            "desc": str(r.get("desc") or "").strip(),
        })
    return out


def merge_relations(known, ai_rel, names):
    """真实外键优先；AI 结果只收「表名都在范围内」且与真实外键不冲突的，去重后返回。"""
    out = []
    seen = set()

    def _add(r, source):
        k = (r["from"], r["from_col"], r["to"], r["to_col"])
        if k in seen:
            return False
        seen.add(k)
        item = dict(r)
        item["source"] = source
        out.append(item)
        return True

    for r in (known or []):
        _add(r, "db")

    for r in (ai_rel or []):
        if r["from"] not in names or r["to"] not in names:
            continue                                     # 越界（编造的表）直接丢
        # 同一个「引用方字段」真实外键已表态 → 以真实外键为准
        if any(x["from"] == r["from"] and x["from_col"] == r["from_col"] for x in out):
            continue
        _add(r, "ai")

    return out


def describe_relations(tables, known_rels=None, db_desc: str = "", config=None, timeout=None):
    """用 AI 兜底推断表关系（真实外键由调用方作为 known_rels 传入，必定保留）。

    返回 {"relations": [...], "usage_in": n, "usage_out": n, "latency_ms": n}；
    调用失败抛 llm.LLMError，由上层转成友好的 toast。
    """
    tables = tables or []
    known = scoped_rels(tables, known_rels)
    if not tables:
        return {"relations": known, "usage_in": 0, "usage_out": 0, "latency_ms": 0}

    cfg = config or ai_config()
    if not cfg["base_url"] or not cfg["model"]:
        raise llm.LLMError("未配置硅基流动基址或模型（请检查 config/settings.py）")

    messages = [
        {"role": "system", "content": RELATION_SYSTEM},
        {"role": "user", "content": build_relation_prompt(db_desc, tables, known)},
    ]
    content, usage, ms, _raw = llm.chat(
        cfg["base_url"], cfg["api_key"], cfg["model"], messages,
        temperature=0, timeout=timeout or cfg["timeout"],
    )
    parsed = parse_relations(content)
    if parsed is None:
        raise llm.LLMError("模型未返回可解析的 JSON 表关系")

    names = {str(t.get("name") or "") for t in tables}
    return {
        "relations": merge_relations(known, parsed, names),
        "usage_in": usage["in"], "usage_out": usage["out"], "latency_ms": ms,
    }
