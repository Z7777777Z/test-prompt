"""
Django 配置 · Test Prompt 2.0
- 数据库：SQLite，文件 Prompt_Test_DB.db（与 tp/db.py 共用同一文件）
- 本页只用到「渲染原型页 + 读写自有表」，故 INSTALLED_APPS 保持最小
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------
# 部署相关配置：全部走环境变量，容器/创空间里改配置不必改代码。
#   TP_SECRET_KEY  生产密钥（不填则用本地开发用占位值）
#   TP_DEBUG=1     打开调试页（默认关闭）
#   TP_ALLOWED_HOSTS  逗号分隔；默认 * （创空间的域名不固定，故放开）
# ------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "TP_SECRET_KEY", "test-prompt-2.0-local-dev-key-not-for-production"
)
DEBUG = os.environ.get("TP_DEBUG", "0") == "1"

_hosts = os.environ.get("TP_ALLOWED_HOSTS", "*").strip()
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()] or ["*"]

# 创空间通过反向代理访问，POST 接口（保存/删除/评估）在视图上已 csrf_exempt，
# 这里再放开可信来源，避免走 https 域名时被拦。
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("TP_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# ------------------------------------------------------------
# 硅基流动（「表结构」页 · 生成表结构说明时，为数据库里没有注释的表/字段补充备注）
#   协议：OpenAI 兼容 /chat/completions
#   注意 model 必须写服务商侧的完整 id（带命名空间）。口语名 `deepseek-v4-pro`
#   在硅基流动不存在，会报 HTTP 400 · code 20012，故默认使用
#   `deepseek-ai/DeepSeek-V4-Pro`（与 config/judge_config.json 同一口径）。
#   均可用环境变量覆盖，便于换 key / 换模型而不改代码。
# ------------------------------------------------------------
SILICONFLOW_BASE_URL = os.environ.get(
    "TP_SF_BASE_URL", "https://api.siliconflow.cn/v1"
).strip()
SILICONFLOW_MODEL = os.environ.get(
    "TP_SF_MODEL", "deepseek-ai/DeepSeek-V4-Pro"
).strip()
# 创空间为公开仓库，密钥一律不写进代码：请在该创空间的「环境变量/Secrets」里配置 TP_SF_API_KEY
SILICONFLOW_API_KEY = os.environ.get("TP_SF_API_KEY", "").strip()
try:
    SILICONFLOW_TIMEOUT = int(os.environ.get("TP_SF_TIMEOUT", "180"))
except (TypeError, ValueError):
    SILICONFLOW_TIMEOUT = 180

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "tp",                       # 业务应用（视图/路由/管理命令）；无 models.py，业务表由 tp/db.py 直连管理
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [],
            # 显式使用「不缓存」的加载器：Django 6.x 在 DEBUG 下仍会缓存已编译模板，
            # 会导致改了 templates/*.html 不重启看不到效果。开发期明确关掉缓存。
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Django 自身不托管业务表（业务表由 tp/db.py 用 sqlite3 直连管理），
# 这里仅指向同一个文件，保证 DATABASES 配置合法。
# 路径统一从 tp.db 取，避免「容器里 DB 落在 /mnt/workspace、配置却指向别处」的不一致。
from tp import db as _db  # noqa: E402

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _db.DB_PATH,
    }
}

STATIC_URL = "static/"

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
