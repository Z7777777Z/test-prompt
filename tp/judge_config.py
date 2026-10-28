"""模型判定（裁判）模型配置
------------------------------------------------------------
「评估」链路里有两类模型，职责严格分开：

  · 评估对象模型 —— 由页面「选择模型」选定（model_id + api_key + 系统提示词），
    负责对问题列表里的每条问题生成「模型答案」；
  · 判定模型     —— 由本配置文件指定（与评估对象无关），
    负责判定「标准答案 vs 模型答案」是否一致（文本含义一致即可、代码功能一致即可）。

配置来源（优先级由低到高）：
  1. 内置默认值 DEFAULTS（仅兜底，不含 api_key）
  2. 配置文件：默认 config/judge_config.json（可用 TP_JUDGE_CONFIG 指向别的路径）
  3. 环境变量：TP_JUDGE_BASE_URL / TP_JUDGE_MODEL / TP_JUDGE_API_KEY / TP_JUDGE_TIMEOUT

api_key 一律只写在配置文件或环境变量里，代码中不出现明文。
配置文件每次调用时实时读取（体积很小），改完内容无需重启服务即可生效。
"""
import json
import os
from pathlib import Path

# 项目根目录（tp 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 默认配置文件路径；TP_JUDGE_CONFIG 可覆盖（便于容器/创空间挂载不同的配置）
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "judge_config.json"

# 兜底默认值：base_url / model 给出公开口径，api_key 留空（缺 key 时由接口返回鉴权错误）
# 注意 model 必须是服务商侧的完整模型 id（硅基流动为 deepseek-ai/DeepSeek-V4-Pro 这种带命名空间的写法）
DEFAULTS = {
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-V4-Pro",
    "api_key": "",
    "timeout": 120,
}


def config_path() -> str:
    """当前使用的配置文件路径。"""
    return os.environ.get("TP_JUDGE_CONFIG") or str(DEFAULT_CONFIG_PATH)


def load() -> dict:
    """读取判定模型配置；文件缺失/损坏时回落默认值，不抛异常。"""
    cfg = dict(DEFAULTS)

    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULTS:
                val = data.get(key)
                if val not in (None, ""):
                    cfg[key] = val
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        # 配置文件不是合法 JSON / 读不了：继续用默认值，避免整条评估链路挂掉
        pass

    # 环境变量优先级最高
    env_map = {
        "base_url": "TP_JUDGE_BASE_URL",
        "model": "TP_JUDGE_MODEL",
        "api_key": "TP_JUDGE_API_KEY",
        "timeout": "TP_JUDGE_TIMEOUT",
    }
    for key, env_name in env_map.items():
        val = os.environ.get(env_name)
        if val:
            cfg[key] = val

    # timeout 统一成 int（配置里写成字符串也不至于出错）
    try:
        cfg["timeout"] = int(cfg.get("timeout") or DEFAULTS["timeout"])
    except (TypeError, ValueError):
        cfg["timeout"] = DEFAULTS["timeout"]

    cfg["base_url"] = str(cfg.get("base_url") or "").strip()
    cfg["model"] = str(cfg.get("model") or "").strip()
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    return cfg


def describe() -> dict:
    """对外可见的配置摘要：不含 api_key，只说明「有没有配 key」。"""
    cfg = load()
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg["api_key"]),
        "config_path": config_path(),
    }
