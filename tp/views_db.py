"""「表结构」页 · 数据库接口。

  POST /api/db/connect/      —— 真实连接用户填写的库，返回其表结构
  POST /api/schema/desc/     —— 生成表结构说明：库中没有注释的表/字段，用硅基流动 AI 补充备注

请求体（JSON）—— 与弹窗字段一一对应：
  {
    "type":  "MySQL" | "MariaDB" | "PostgreSQL" | "SQLite" | ...   # 数据库类型
    "mode":  "host" | "url"                                        # 连接方式
    "host":  "127.0.0.1",     # 主机模式：服务器地址
    "port":  "3306",          # 主机模式：端口（留空用各库默认端口）
    "url":   "jdbc:mysql://...",  # URL 模式：URL 原文
    "user":  "root",          # 用户名
    "password": "******",     # 密码（仅用于本次连接，不落库）
    "path":  "C:/x.db"        # SQLite：数据库文件路径
  }

响应（JSON）：
  成功 {"ok": true, "server_id": "127.0.0.1:3306", "dbs": [ ...左侧树结构... ],
        "db_count": 1, "table_count": 4}
  失败 {"ok": false, "error": "连不上 MySQL 服务（127.0.0.1:3306）：..."}

设计要点：
  · 真的去连用户填的库，连不上就把原因原样告诉用户，不再拿项目自带库的数据冒充。
  · 连接信息只用于这一次连接，密码不写库、不落日志。
  · Oracle / SQL Server 等缺驱动的类型，返回明确提示而非静默失败。
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import schema_desc as schema_ai
from . import sql_conn


@csrf_exempt
def db_connect(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"})

    db_type = (data.get("type") or "").strip()
    try:
        dbs = sql_conn.introspect(db_type, data)
    except sql_conn.ConnError as e:
        return JsonResponse({"ok": False, "error": str(e)})
    except Exception as e:                       # noqa: BLE001 —— 兜底，避免 500 白屏
        return JsonResponse({"ok": False, "error": "连接失败：%s" % e})

    table_count = sum(len(d.get("tables") or []) for d in dbs)
    if not dbs:
        return JsonResponse({
            "ok": False,
            "error": "已连上，但该账号下没有可见的数据库（MySQL 请检查账号权限）。",
        })

    return JsonResponse({
        "ok": True,
        "server_id": sql_conn.server_id(db_type, data),
        "dbs": dbs,
        "db_count": len(dbs),
        "table_count": table_count,
    })


# ------------------------------------------------------------
# 「表结构」页 · 生成表结构说明：数据库里没有的表/字段备注，用硅基流动 AI 补充
# ------------------------------------------------------------
def _norm_tables(raw):
    """把前端传来的表结构规范化成 schema_ai 需要的形态，顺手挡掉畸形数据。"""
    out = []
    for t in (raw or []):
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        cols = []
        for c in (t.get("cols") or []):
            if not isinstance(c, (list, tuple)):
                continue
            row = list(c[:6]) + [""] * (6 - len(c))
            if not str(row[0] or "").strip():
                continue
            cols.append([
                str(row[0]), str(row[1] or ""), 1 if row[2] else 0,
                str(row[3] or ""), str(row[4] or ""), str(row[5] or ""),
            ])
        out.append({"name": name, "cn": str(t.get("cn") or ""), "cols": cols})
    return out


@csrf_exempt
def schema_desc(request):
    """POST /api/schema/desc/

    请求体：
      {
        "db_desc": "数据库用途描述（可选）",
        "tables": [{"name": 表名, "cn": 表说明,
                    "cols": [[字段名, 类型, 是否必填, 键, 默认值, 注释], ...]}],
        "rels": [{"from": 表名, "from_col": 列, "to": 表名, "to_col": 列}, ...]
                —— 可选：前端从「连接数据库」结果里带过来的**真实外键**，一定保留
      }

    响应：
      成功 {"ok": true, "tables": [ ...同上结构，已补充缺失的表说明/字段备注... ],
            "relations": [{"from","from_col","to","to_col","type","desc","source"}],
            "filled": 5, "model": "deepseek-ai/DeepSeek-V4-Pro",
            "usage": {"in": 1, "out": 2}, "latency_ms": 1234,
            "relation_warning": "AI 补充表关系失败：..."   # 仅关系环节失败时才有
           }
      失败 {"ok": false, "error": "..."}   —— 一律 200，绝不 500

    口径：只补「库里没有备注」的位置，库里已有的原样保留；只描述表/字段存储的内容。
    表关系：真实外键优先，只有覆盖不全（有表既不被引用也不引用别人）时才让 AI 兜底。
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"})

    tables = _norm_tables(data.get("tables"))
    if not tables:
        return JsonResponse({"ok": False, "error": "没有需要生成说明的表"})

    cfg = schema_ai.ai_config()
    try:
        result = schema_ai.describe(data.get("db_desc") or "", tables, config=cfg)
    except Exception as e:                       # noqa: BLE001 —— 兜底，避免 500 白屏
        return JsonResponse({"ok": False, "error": "AI 补充备注失败：%s" % e})

    # —— 表关系：真实外键优先，AI 兜底 ——
    names = {t["name"] for t in tables}
    known = schema_ai.scoped_rels(tables, data.get("rels"))
    relations = schema_ai.merge_relations(known, [], names)
    relation_warning = ""
    usage_in = result["usage_in"]
    usage_out = result["usage_out"]
    latency = result["latency_ms"]

    if schema_ai.need_ai_rels(tables, known):
        try:
            rel_res = schema_ai.describe_relations(
                tables, known, data.get("db_desc") or "", config=cfg)
            relations = rel_res["relations"]
            usage_in += rel_res["usage_in"]
            usage_out += rel_res["usage_out"]
            latency += rel_res["latency_ms"]
        except Exception as e:                   # noqa: BLE001 —— 关系失败不影响备注结果
            relation_warning = "AI 补充表关系失败：%s" % e

    return JsonResponse({
        "ok": True,
        "tables": result["tables"],
        "relations": relations,
        "filled": result["filled"],
        "model": cfg.get("model") or "",
        "usage": {"in": usage_in, "out": usage_out},
        "latency_ms": latency,
        "errors": result["errors"],
        "relation_warning": relation_warning,
    })
