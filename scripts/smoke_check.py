"""冒烟检查：在无浏览器的环境里验证应用能起、页面能出、接口能写。

CI（GitHub Actions）和本地提交前都用它：

    python scripts/smoke_check.py

覆盖：
  1. 建表 + 示例数据（与容器启动逻辑一致）
  2. 3 个业务页面 + 4 个原型页返回 200，/ 正确跳转
  3. 保存 → 同条更新 → 评估 → 删除 全链路写入
  4. SQLite 表结构内省
任一环节失败即以非 0 退出码结束。
"""
import os
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# 冒烟一律用临时库，绝不碰仓库里的数据文件
os.environ.setdefault("TP_DB_PATH", os.path.join(tempfile.mkdtemp(), "smoke.db"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from tp import db  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  |  " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


print("[1/4] 初始化数据库：%s" % db.DB_PATH)
db.init_db()
db.seed_prompts()
stats = db.evaluate_stats_map()
check("服务器种子数据", len(db.servers_for_js()) >= 10, len(db.servers_for_js()))
check("模型种子数据", len(db.models_for_js()) >= 20, len(db.models_for_js()))
check("提示词种子数据", len(db.list_prompts()) >= 1, len(db.list_prompts()))
check("评估种子数据", sum(v["total"] for v in stats.values()) >= 1)

print("[2/4] 页面可达性")
c = Client()
check("/ 跳转", c.get("/").status_code == 302)
for path in ("/prompt_dis_2.0.html", "/prompt_list.html", "/table_relation.html",
             "/list_server.html", "/server_add.html",
             "/model_server.html", "/model_add.html"):
    r = c.get(path)
    check(path, r.status_code == 200 and len(r.content) > 1000, len(r.content))

print("[3/4] 写入链路")
import json  # noqa: E402

r = c.post("/api/prompt/save/", data=json.dumps({
    "prompt_name": "smoke-检查", "model_id": 1,
    "sys_prompt": "冒烟检查系统提示词", "user_prompt1": "冒烟问题",
    "u_promt_count": 1,
}), content_type="application/json")
check("保存新记录", r.status_code == 200 and r.json().get("ok"), r.json())
pid = r.json().get("id")

r = c.post("/api/prompt/save/", data=json.dumps({
    "id": pid, "prompt_name": "smoke-检查-改", "model_id": 1,
    "sys_prompt": "冒烟检查系统提示词 2", "user_prompt1": "冒烟问题",
    "u_promt_count": 1,
}), content_type="application/json")
check("同条更新", r.status_code == 200 and r.json().get("updated") is True, r.json())

r = c.post("/api/evaluate/save/", data=json.dumps({
    "prompt_id": pid,
    "rows": [{"question": "q", "answer": "a", "m_answer": "a",
              "is_true": 1, "is_correct": 0}],
}), content_type="application/json")
check("写入评估结果", r.status_code == 200 and r.json().get("saved") == 1, r.json())

r = c.post("/api/prompt/delete/", data=json.dumps({"ids": [pid]}),
           content_type="application/json")
check("删除记录", r.status_code == 200 and r.json().get("deleted") == 1, r.json())

print("[4/4] 数据库内省")
r = c.post("/api/db/connect/", data=json.dumps({"type": "SQLite", "mode": "host",
                                                "path": db.DB_PATH}),
           content_type="application/json")
payload = r.json()
check("SQLite 连接与表结构", payload.get("ok") and payload.get("table_count", 0) == 4,
      payload.get("error") or payload.get("table_count"))

print()
if FAILED:
    print("冒烟未通过，失败项：%s" % ", ".join(FAILED))
    sys.exit(1)
print("冒烟全部通过（Django %s）" % django.get_version())
