#!/bin/sh
# ============================================================
# 魔搭创空间 Docker 实例启动脚本
#   1) 选定数据库位置：优先平台持久化目录 /mnt/workspace（重启不丢），不可写则回落容器内
#   2) 建表 + 灌入服务器/模型/提示词示例数据（幂等，已有数据不会覆盖）
#   3) 用 gunicorn 监听 0.0.0.0:7860（魔搭要求的固定端口，不能改成 8080）
# ============================================================
set -e

cd /home/user/app

echo "[test-prompt] ===== 启动准备 ====="

# ---- 1) 数据库位置：持久化目录可写才用，否则回落（并明确提示会丢数据） ----
if mkdir -p /mnt/workspace 2>/dev/null && touch /mnt/workspace/.tp_write_test 2>/dev/null; then
  rm -f /mnt/workspace/.tp_write_test
  export TP_DB_PATH="${TP_DB_PATH:-/mnt/workspace/Prompt_Test_DB.db}"
  echo "[test-prompt] 持久化目录可用：/mnt/workspace"
else
  export TP_DB_PATH="${TP_DB_PATH:-/home/user/app/Prompt_Test_DB.db}"
  echo "[test-prompt] 警告：/mnt/workspace 不可写，数据库回落容器内（重启后数据会丢失）"
fi

# ---- 2) 建表 + 示例数据（幂等） ----
python - <<'PY'
import sys
sys.path.insert(0, ".")
from tp import db

print("[test-prompt] 数据库文件:", db.DB_PATH)
db.init_db()                     # 建表 + 服务器/模型演示数据（幂等）
n = db.seed_prompts()            # 调试列表示例数据（表非空时返回 0，不会重复插）
print("[test-prompt] 本轮新增示例提示词:", n, "条")

stats = db.evaluate_stats_map()
print("[test-prompt] 现有记录数: 服务器 %d / 模型 %d / 提示词 %d / 评估 %d" % (
    len(db.servers_for_js()), len(db.models_for_js()), len(db.list_prompts()),
    sum(v.get("total", 0) for v in stats.values())))
PY

# ---- 3) 起服务 ----
# timeout 放宽到 300s：批量调试会逐条真实调用大模型，单批可能超过 120s
echo "[test-prompt] ===== 启动 gunicorn @ 0.0.0.0:7860 ====="
exec python -m gunicorn config.wsgi:application \
  --chdir /home/user/app \
  --bind 0.0.0.0:7860 \
  --worker-class gthread \
  --workers 1 \
  --threads 8 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
