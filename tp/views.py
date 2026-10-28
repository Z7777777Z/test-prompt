"""
视图层
- prompt_dis    : 「提示词调试」页（prompt_dis_2.0.html 原型）
- prompt_list   : 「调试列表」页（prompt_list.html 原型）
- table_relation: 「表结构」页（table_relation.html 原型）
- prompt_save   : 保存按钮 → 写入 prompt 表
- prompt_delete : 列表页批量删除
- evaluate_save : 调试页「调试结果」的「评估」按钮 → 写入 evaluate 表
- proto         : 尚未开发的原型页，按原样返回（保证左侧菜单不 404）
"""
import json
import os

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from . import db

PROTO_DIR = os.path.join(db.BASE_DIR, "prototypes")
PROTO_FILES = {
    "list_server.html", "server_add.html",
    "model_server.html", "model_add.html",
}


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


def index(_request):
    return redirect("prompt_dis")


# ------------------------------------------------------------
# 提示词调试
# ------------------------------------------------------------
def prompt_dis(request):
    db.init_db()

    # ?open=<id> —— 从「调试列表」点「编辑」/「评估」进入时回填当时保存的内容
    open_snapshot = None
    pk = request.GET.get("open")
    if pk and str(pk).isdigit():
        row = db.get_prompt(int(pk))
        if row:
            open_snapshot = db.prompt_to_snapshot(row)

    # ?focus=eval —— 从列表「评估」进入时，页面自动定位到「调试结果」模块区域
    focus = request.GET.get("focus") or ""

    return render(request, "prompt_dis.html", {
        "servers_json": _j(db.servers_for_js()),
        "models_json": _j(db.models_for_js()),
        "open_json": _j(open_snapshot),
        "focus_json": _j(focus),
    })


# ------------------------------------------------------------
# 调试列表
# ------------------------------------------------------------
def prompt_list(request):
    db.init_db()
    keyword = request.GET.get("kw", "")

    # 「用例总数 / 准确率」来自 evaluate 表：total = 评估用例数，
    # 准确率 = 判定正确 ÷ 已判定（待判定的不计入），无已判定记录时显示 --
    stats_map = db.evaluate_stats_map()

    records = []
    for r in db.list_prompts(keyword):
        st = stats_map.get(r["id"], {"total": 0, "judged": 0, "ok": 0})
        rate = round(st["ok"] / st["judged"] * 100, 1) if st["judged"] else None
        records.append({
            "id": str(r["id"]),
            "name": r.get("prompt_name") or "未命名调试",
            "server": r.get("server") or "",
            "model": r.get("model_name") or "",
            "sysPrompt": r.get("sys_prompt") or "",
            "savedAt": r.get("created_at") or "",
            "stats": {"total": st["total"], "judged": st["judged"], "rate": rate},
        })

    return render(request, "prompt_list.html", {
        "records_json": _j(records),
        "server_home_json": _j(db.server_home_map()),
        "keyword": keyword,
    })


# ------------------------------------------------------------
# 表结构
# ------------------------------------------------------------
def table_relation(request):
    """表结构页。左侧「服务器 → 数据库 → 表」不再是服务端注入的演示数据：
    由用户点「连接数据库」后，前端调 /api/db/connect/ 真实连接并现场读取目标库的表结构。"""
    return render(request, "table_relation.html")


# ------------------------------------------------------------
# 保存 / 删除
# ------------------------------------------------------------
@csrf_exempt
def prompt_save(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"}, status=400)

    model_id = data.get("model_id")
    try:
        model_id = int(model_id) if model_id not in (None, "") else None
    except (TypeError, ValueError):
        model_id = None

    counts = data.get("u_promt_count")
    try:
        counts = int(counts) if counts not in (None, "") else 0
    except (TypeError, ValueError):
        counts = 0

    payload = {
        "model_id": model_id,
        "prompt_name": (data.get("prompt_name") or "").strip(),
        "api_key": (data.get("api_key") or "").strip(),
        "sys_prompt": data.get("sys_prompt") or "",
        "u_promt_count": counts,
    }
    for i in range(1, 7):
        payload[f"user_prompt{i}"] = data.get(f"user_prompt{i}") or ""

    # 保存口径对齐原型 saveRecord()：「同一条记录就地更新，新编辑则新增一条」。
    # 前端回传的 id 是当前记录在 prompt 表的 id（从「调试列表」打开、或上一次保存后记下的）；
    # 记录已不存在（如刚被删除）时回落为新增。
    pk = data.get("id")
    try:
        pk = int(pk) if pk not in (None, "") else None
    except (TypeError, ValueError):
        pk = None
    if pk and db.update_prompt(pk, payload):
        return JsonResponse({"ok": True, "id": pk, "updated": True})

    return JsonResponse({"ok": True, "id": db.insert_prompt(payload), "updated": False})


@csrf_exempt
def prompt_delete(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
    ids = data.get("ids") or []
    n = db.delete_prompts(ids)
    return JsonResponse({"ok": True, "deleted": n})


# ------------------------------------------------------------
# 评估：调试页「调试结果」的「评估」按钮 → 写入 evaluate 表
# ------------------------------------------------------------
@csrf_exempt
def evaluate_save(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"}, status=400)

    try:
        prompt_id = int(data.get("prompt_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "缺少关联的调试记录（prompt_id）"}, status=400)

    rows = []
    for r in data.get("rows") or []:
        def _int(v):
            # 待判定 -> None；仅接受 1/0
            if v in (None, ""):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        rows.append({
            "question": r.get("question") or "",
            "answer": r.get("answer") or "",
            "m_answer": r.get("m_answer") or "",
            "is_true": _int(r.get("is_true")),
            "is_correct": _int(r.get("is_correct")),
        })

    saved = db.save_evaluations(prompt_id, rows)
    return JsonResponse({"ok": True, "saved": saved, "prompt_id": prompt_id})


# ------------------------------------------------------------
# 尚未开发的原型页：原样返回
# ------------------------------------------------------------
def proto(_request, name):
    if name not in PROTO_FILES:
        return HttpResponse("Not Found", status=404)
    path = os.path.join(PROTO_DIR, name)
    if not os.path.exists(path):
        return HttpResponse("Not Found", status=404)
    with open(path, "r", encoding="utf-8") as f:
        return HttpResponse(f.read(), content_type="text/html; charset=utf-8")


def score_stub(_request):
    """「评估列表」尚未开发，给一个占位页，避免 404。"""
    return HttpResponse(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
        "<title>Test Prompt · 评估列表</title></head>"
        "<body style='font:14px/1.6 -apple-system,\"PingFang SC\",\"Microsoft YaHei\",sans-serif;"
        "padding:48px;color:#1f2329;background:#f6f7f9'>"
        "<h2 style='margin:0 0 8px'>评估列表（待开发）</h2>"
        "<p style='color:#5b6371'>该模块将在「评估」迭代中实现。"
        "<a href='/prompt_list.html' style='color:#2c6bed'>返回调试列表</a></p>"
        "</body></html>",
        content_type="text/html; charset=utf-8",
    )
