"""
Excel(.xlsx) 用例导入视图
------------------------------------------------------------
「Excel 导入」按钮选好文件后 POST 到这里，服务端解析出
「问题 + 标准答案」两列回给前端，塞进「调试结果」表，再点「批量调试」逐条真实调试。
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import xlsx

MAX_BYTES = 5 * 1024 * 1024      # 5MB：上百条用例的表格远小于此


@csrf_exempt
def import_xlsx(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)

    f = request.FILES.get("file")
    if f is None:
        return JsonResponse({"ok": False, "error": "没有收到文件"}, status=400)
    if f.size and f.size > MAX_BYTES:
        return JsonResponse({"ok": False, "error": "文件过大（超过 5MB）"}, status=400)

    try:
        cases = xlsx.parse_cases(f.read())
    except Exception as e:                       # noqa: BLE001
        return JsonResponse({"ok": False, "error": "解析失败：%s" % (e,)}, status=400)

    if not cases:
        return JsonResponse(
            {"ok": False, "error": "未解析到「问题 / 标准答案」两列，请检查表头或前两列内容"},
            status=400,
        )

    return JsonResponse({"ok": True, "cases": cases, "count": len(cases)})
