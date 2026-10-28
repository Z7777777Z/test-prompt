"""
数据访问层 · SQLite
数据库：Prompt_Test_DB
文件：Prompt_Test_DB.db（项目根目录）

表：
  tp_server  服务器（平台/官方名、base_url、公司名、首页）
  tp_model   模型（归属服务器、模型名、输入/输出计费与单位、类型、网址）
  prompt     提示词调试记录（字段严格对齐需求说明：
             id / model_id / prompt_name / api_key / sys_prompt /
             user_prompt1..6 / u_promt_count）
  evaluate   提示词评估结果（字段严格对齐需求说明：
             id / prompt_id / question / answer / m_answer / is_true / is_correct）
"""
import datetime
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库文件位置，按优先级取：
#   1) 环境变量 TP_DB_PATH 显式指定（最高优先级）
#   2) 容器内的持久化目录 /mnt/workspace —— 魔搭创空间 Docker 实例重启后
#      默认文件系统会清空，只有这个目录的数据能留下
#   3) 项目根目录 —— 本地开发
# 三种情况下文件名都保持 Prompt_Test_DB.db。
PERSIST_DIR = "/mnt/workspace"
DB_PATH = (
    os.environ.get("TP_DB_PATH")
    or os.path.join(PERSIST_DIR if os.path.isdir(PERSIST_DIR) else BASE_DIR,
                    "Prompt_Test_DB.db")
)

# ------------------------------------------------------------
# 建表语句
# ------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS `tp_server` (
  `id`          INTEGER PRIMARY KEY AUTOINCREMENT,
  `pf_name`     VARCHAR(100)    NOT NULL,
  `base_url`    VARCHAR(255)    NOT NULL,
  `comp_name`   VARCHAR(100)    DEFAULT NULL,
  `home_url`    VARCHAR(255)    DEFAULT NULL,
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `uk_pf_name` ON `tp_server` (`pf_name`);

CREATE TABLE IF NOT EXISTS `tp_model` (
  `id`              INTEGER PRIMARY KEY AUTOINCREMENT,
  `srv_id`          INTEGER       NOT NULL REFERENCES `tp_server`(`id`)
                                  ON UPDATE CASCADE ON DELETE RESTRICT,
  `model_name`      VARCHAR(150)  NOT NULL,
  `in_price`        DECIMAL(12,4) DEFAULT NULL,
  `out_price`       DECIMAL(12,4) DEFAULT NULL,
  `price_unit`      VARCHAR(10)   NOT NULL DEFAULT '百万',
  `price_unit_out`  VARCHAR(10)   NOT NULL DEFAULT '百万',
  `model_type`      VARCHAR(50)   NOT NULL DEFAULT '对话',
  `api_url`         VARCHAR(255)  DEFAULT NULL,
  `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS `uk_srv_model` ON `tp_model` (`srv_id`, `model_name`);
CREATE INDEX IF NOT EXISTS `idx_srv_id` ON `tp_model` (`srv_id`);

-- 提示词调试记录（本次迭代新增）
CREATE TABLE IF NOT EXISTS `prompt` (
  `id`              INTEGER PRIMARY KEY AUTOINCREMENT,
  `model_id`        INTEGER        DEFAULT NULL,
  `prompt_name`     VARCHAR(100)   DEFAULT NULL,
  `api_key`         VARCHAR(255)   DEFAULT NULL,
  `sys_prompt`      TEXT           DEFAULT NULL,
  `user_prompt1`    VARCHAR(255)   DEFAULT NULL,
  `user_prompt2`    VARCHAR(255)   DEFAULT NULL,
  `user_prompt3`    VARCHAR(255)   DEFAULT NULL,
  `user_prompt4`    VARCHAR(255)   DEFAULT NULL,
  `user_prompt5`    VARCHAR(255)   DEFAULT NULL,
  `user_prompt6`    VARCHAR(255)   DEFAULT NULL,
  `u_promt_count`   INTEGER        DEFAULT 0,
  `created_at`      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS `idx_prompt_model_id` ON `prompt` (`model_id`);

-- 提示词评估表（本次迭代新增，字段严格对齐需求说明：
--   id / prompt_id / question / answer / m_answer / is_true / is_correct）
CREATE TABLE IF NOT EXISTS `evaluate` (
  `id`          INTEGER PRIMARY KEY AUTOINCREMENT,
  `prompt_id`   INTEGER       DEFAULT NULL,
  `question`    VARCHAR(255)  DEFAULT NULL,
  `answer`      VARCHAR(255)  DEFAULT NULL,
  `m_answer`    VARCHAR(255)  DEFAULT NULL,
  `is_true`     INTEGER       DEFAULT NULL,
  `is_correct`  INTEGER       DEFAULT NULL,
  CONSTRAINT `fk_evaluate_prompt` FOREIGN KEY (`prompt_id`) REFERENCES `prompt` (`id`)
    ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS `idx_evaluate_prompt_id` ON `evaluate` (`prompt_id`);
"""

# ------------------------------------------------------------
# 初始数据：与 prompt_dis_2.0.html 原型内的 SERVERS / MODELS 完全一致
#   servers: (pf_name, base_url, comp_name, home_url)
# ------------------------------------------------------------
SEED_SERVERS = [
    ("OpenAI",        "https://api.openai.com/v1",                         "OpenAI",     "https://openai.com"),
    ("Anthropic",     "https://api.anthropic.com/v1",                      "Anthropic",  "https://www.anthropic.com"),
    ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta",  "Google",     "https://ai.google.dev"),
    ("DeepSeek",      "https://api.deepseek.com/v1",                       "深度求索",    "https://www.deepseek.com"),
    ("阿里云百炼",     "https://dashscope.aliyuncs.com/compatible-mode/v1", "阿里巴巴",    "https://bailian.console.aliyun.com"),
    ("智谱AI",        "https://open.bigmodel.cn/api/paas/v4",              "智谱华章",    "https://open.bigmodel.cn"),
    ("Moonshot",      "https://api.moonshot.cn/v1",                        "月之暗面",    "https://www.moonshot.cn"),
    ("火山方舟",       "https://ark.cn-beijing.volces.com/api/v3",          "字节跳动",    "https://www.volcengine.com/product/ark"),
    ("百度千帆",       "https://qianfan.baidubce.com/v2",                   "百度",       "https://qianfan.cloud.baidu.com"),
    ("腾讯混元",       "https://api.hunyuan.cloud.tencent.com/v1",          "腾讯",       "https://cloud.tencent.com/product/hunyuan"),
    ("硅基流动",       "https://api.siliconflow.cn/v1",                     "硅基流动",    "https://cloud.siliconflow.cn"),
    ("魔搭社区",       "https://api-inference.modelscope.cn/v1",            "阿里巴巴",    "https://modelscope.cn"),
    ("OpenRouter",    "https://openrouter.ai/api/v1",                      "OpenRouter", "https://openrouter.ai"),
]

#   models: (platform, model_name, in_price, in_unit, out_price, out_unit, model_type)
SEED_MODELS = [
    # OpenAI
    ("OpenAI", "gpt-4o", 18.00, "百万", 72.00, "百万", "对话"),
    ("OpenAI", "gpt-4o-mini", 1.10, "百万", 4.40, "百万", "对话"),
    ("OpenAI", "text-embedding-3-large", 0.94, "百万", 0.00, "百万", "嵌入"),
    ("OpenAI", "gpt-image-1", 36.00, "百万", 288.00, "百万", "生图"),
    ("OpenAI", "gpt-4.1-mini", 3.00, "百万", 12.00, "百万", "对话"),
    ("OpenAI", "text-embedding-3-small", 0.13, "百万", 0.00, "百万", "嵌入"),
    ("OpenAI", "sora-2", 0.00, "十万", 0.00, "十万", "视频"),
    ("OpenAI", "whisper-1", 0.00, "万", 0.00, "万", "语音"),
    # 硅基流动
    ("硅基流动", "deepseek-ai/DeepSeek-V3", 2.00, "百万", 8.00, "百万", "对话"),
    ("硅基流动", "Qwen/Qwen2.5-72B-Instruct", 4.13, "百万", 4.13, "百万", "对话"),
    ("硅基流动", "BAAI/bge-m3", 0.35, "百万", 0.00, "百万", "嵌入"),
    ("硅基流动", "BAAI/bge-reranker-v2-m3", 0.35, "百万", 0.00, "百万", "重排"),
    ("硅基流动", "Pro/deepseek-ai/DeepSeek-R1", 4.00, "百万", 16.00, "百万", "对话"),
    ("硅基流动", "FunAudioLLM/SenseVoiceSmall", 0.00, "万", 0.00, "万", "语音"),
    ("硅基流动", "Qwen/Qwen3-Embedding-8B", 0.50, "百万", 0.00, "百万", "嵌入"),
    ("硅基流动", "Kwai-Kolors/Kolors", 0.00, "十万", 0.00, "十万", "生图"),
    # 魔搭社区
    ("魔搭社区", "Qwen/Qwen3-235B-A22B", 1.40, "百万", 5.60, "百万", "对话"),
    ("魔搭社区", "iic/nlp_gte_sentence-embedding_chinese-base", 0.00, "百万", 0.00, "百万", "嵌入"),
    ("魔搭社区", "CosyVoice-300M", 0.00, "万", 0.00, "万", "语音"),
    ("魔搭社区", "Qwen/Qwen3-Reranker-8B", 0.70, "百万", 0.00, "百万", "重排"),
    ("魔搭社区", "Qwen/Qwen2.5-VL-72B-Instruct", 5.00, "百万", 15.00, "百万", "对话"),
    ("魔搭社区", "Wan-AI/Wan2.2-T2V-A14B", 0.00, "十万", 0.00, "十万", "视频"),
    ("魔搭社区", "Qwen/Qwen3-4B", 0.30, "百万", 1.20, "百万", "对话"),
    # OpenRouter
    ("OpenRouter", "anthropic/claude-3.7-sonnet", 21.60, "百万", 108.00, "百万", "对话"),
    ("OpenRouter", "google/gemini-2.0-flash-001", 0.72, "百万", 2.88, "百万", "对话"),
    ("OpenRouter", "meta-llama/llama-4-maverick", 1.80, "百万", 7.20, "百万", "对话"),
    ("OpenRouter", "deepseek/deepseek-r1", 3.60, "百万", 14.40, "百万", "对话"),
    ("OpenRouter", "google/gemini-2.5-pro-preview", 9.00, "百万", 54.00, "百万", "对话"),
    ("OpenRouter", "openai/dall-e-3", 0.00, "十万", 0.00, "十万", "生图"),
    ("OpenRouter", "perplexity/pplx-embed-v1-4b", 0.20, "百万", 0.00, "百万", "嵌入"),
]

# ------------------------------------------------------------
# 示例调试记录（写入 prompt 表）+ 示例评估结果（写入 evaluate 表）
#   前 3 条与原型 prompt_list.html 内置的演示记录逐字一致；
#   后 3 条用于覆盖「多轮对话 / 短系统提示词（≤20 字不截断）/ 长系统提示词（>20 字截断）」
#   等展示场景。created_at 显式给定，让列表「时间」列有真实观感；
#   api_key 统一留空（与原型演示数据一致，也不落任何敏感信息）。
#   evals：该记录「调试结果」表里的用例，评估后写入 evaluate 表，
#          同时也是调试列表「用例总数 / 准确率」两列的数据来源。
# ------------------------------------------------------------
SEED_PROMPTS = [
    {
        "platform": "OpenAI", "model": "gpt-4o",
        "prompt_name": "客服问答调试",
        "sys_prompt": "你是一名专业的客服助手，回答需准确、简洁，不确定时不要编造。",
        "user_prompts": ["你们的退货政策是什么？"],
        "created_at": "2026-09-28 16:42:11",
        "evals": [
            {
                "question": "你们的退货政策是什么？",
                "answer": "自签收之日起 7 天内可无理由退货，商品需保持完好并附带原包装与赠品。",
                "m_answer": "自签收之日起 7 天内可无理由退货，商品需保持完好并附带原包装与赠品。",
                "is_true": 1, "is_correct": 0,
            },
        ],
    },
    {
        "platform": "硅基流动", "model": "deepseek-ai/DeepSeek-V3",
        "prompt_name": "电商文案调试",
        "sys_prompt": "你是电商文案助手，输出不要超过两句话，突出卖点。",
        "user_prompts": ["把这段商品描述改写成 60 字以内的卖点摘要。"],
        "created_at": "2026-09-28 17:05:38",
        "evals": [
            {
                "question": "把这段商品描述改写成 60 字以内的卖点摘要。",
                "answer": "249g 轻量机身，42dB 主动降噪，36 小时长续航，通勤一整天不断电。",
                "m_answer": "249g 轻量机身，42dB 主动降噪，36 小时长续航，通勤一整天不断电。",
                "is_true": 1, "is_correct": 0,
            },
        ],
    },
    {
        "platform": "魔搭社区", "model": "Qwen/Qwen3-235B-A22B",
        "prompt_name": "意图识别调试",
        "sys_prompt": "你是意图识别器，只输出一个类别名，不要解释。",
        "user_prompts": ["帮我查一下订单到哪了"],
        "created_at": "2026-09-29 09:18:02",
        "evals": [
            {
                "question": "帮我查一下订单到哪了",
                "answer": "订单查询",
                "m_answer": "订单查询",
                "is_true": 1, "is_correct": 0,
            },
        ],
    },
    {
        "platform": "OpenRouter", "model": "anthropic/claude-3.7-sonnet",
        "prompt_name": "多轮售前咨询调试",
        "sys_prompt": "你是一名家电售前顾问，需要结合用户历史提问逐步缩小推荐范围，"
                      "先确认使用场景与预算，再给出不超过两款的具体型号建议，"
                      "回答友好、专业，避免堆砌参数。",
        "user_prompts": [
            "我想买一台扫地机器人，有推荐吗？",
            "家里有宠物，掉毛比较多。",
            "预算 3000 元以内，哪个更合适？",
        ],
        "created_at": "2026-10-01 10:23:45",
        "evals": [
            {
                "question": "我想买一台扫地机器人，有推荐吗？",
                "answer": "先确认户型面积与是否有宠物，再在 2000-3000 元档对比吸力与自动集尘能力。",
                "m_answer": "先确认户型面积与是否有宠物，再在 2000-3000 元档对比吸力与自动集尘能力。",
                "is_true": 1, "is_correct": 0,
            },
            {
                "question": "家里有宠物，掉毛比较多。",
                "answer": "优先选带胶刷防缠绕 + 大吸力（≥5000Pa）且支持自动集尘的型号。",
                "m_answer": "优先选带胶刷防缠绕 + 大吸力（≥5000Pa）且支持自动集尘的型号。",
                "is_true": 1, "is_correct": 0,
            },
            {
                "question": "预算 3000 元以内，哪个更合适？",
                "answer": "推荐 A 款（自动集尘 + 胶刷）与 B 款（自清洁基站）两款，前者更适合养宠家庭。",
                "m_answer": "推荐 A 款与 B 款，具体看预算。",
                "is_true": 0, "is_correct": 1,
            },
        ],
    },
    {
        "platform": "OpenAI", "model": "gpt-4o-mini",
        "prompt_name": "商品标题生成",
        "sys_prompt": "你是商品标题生成器。",
        "user_prompts": ["为这款降噪耳机生成 5 个电商标题。"],
        "created_at": "2026-10-02 14:07:20",
        "evals": [
            {
                "question": "为这款降噪耳机生成 5 个电商标题。",
                "answer": "1. 主动降噪无线蓝牙耳机 长续航 入耳式\n2. 42dB 深度降噪耳机 通勤降噪神器\n3. 轻量降噪蓝牙耳机 36 小时续航\n4. 高音质降噪耳机 安卓苹果通用\n5. 静音降噪耳机 游戏低延迟",
                "m_answer": "1. 主动降噪无线蓝牙耳机 长续航 入耳式\n2. 42dB 深度降噪耳机 通勤降噪神器\n3. 轻量降噪蓝牙耳机 36 小时续航\n4. 高音质降噪耳机 安卓苹果通用\n5. 静音降噪耳机 游戏低延迟",
                "is_true": 1, "is_correct": 0,
            },
        ],
    },
    {
        "platform": "魔搭社区", "model": "Qwen/Qwen3-4B",
        "prompt_name": "评论情感分析",
        "sys_prompt": "你是情感分析器，输出 正面/中性/负面 三种标签之一，不要解释。",
        "user_prompts": ["物流很快，包装也很用心，好评！"],
        "created_at": "2026-10-03 11:35:09",
        "evals": [
            {
                "question": "物流很快，包装也很用心，好评！",
                "answer": "正面",
                "m_answer": "这句话表达了积极情绪，属于正面评价。",
                "is_true": 0, "is_correct": 1,
            },
        ],
    },
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(seed: bool = True):
    """建表（幂等）+ 首次写入服务器/模型演示数据。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        if seed and conn.execute("SELECT COUNT(*) c FROM tp_server").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO tp_server (pf_name, base_url, comp_name, home_url) VALUES (?,?,?,?)",
                SEED_SERVERS,
            )
            srv_id = {r["pf_name"]: r["id"]
                      for r in conn.execute("SELECT id, pf_name FROM tp_server").fetchall()}
            conn.executemany(
                "INSERT INTO tp_model (srv_id, model_name, in_price, out_price, "
                "price_unit, price_unit_out, model_type) VALUES (?,?,?,?,?,?,?)",
                [(srv_id[p], m, ip, op, iu, ou, t)
                 for (p, m, ip, iu, op, ou, t) in SEED_MODELS],
            )


# ------------------------------------------------------------
# 供「选择模型」弹窗使用（对齐原型 SERVERS / MODELS 的结构）
# ------------------------------------------------------------
def servers_for_js():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, pf_name, comp_name, base_url FROM tp_server ORDER BY id ASC"
        ).fetchall()
    return [
        {"id": r["id"], "name": r["pf_name"],
         "company": r["comp_name"] or "", "baseUrl": r["base_url"]}
        for r in rows
    ]


def models_for_js():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id, s.pf_name AS platform, m.model_name, m.in_price, m.out_price, "
            "       m.price_unit, m.price_unit_out "
            "FROM tp_model m JOIN tp_server s ON s.id = m.srv_id "
            "ORDER BY m.id ASC"
        ).fetchall()
    return [
        {"id": r["id"], "platform": r["platform"], "model": r["model_name"],
         "inPrice": float(r["in_price"] or 0), "inUnit": r["price_unit"],
         "outPrice": float(r["out_price"] or 0), "outUnit": r["price_unit_out"]}
        for r in rows
    ]


def server_home_map():
    """平台/官方名 -> 首页地址（列表页「平台/官方名」超链接用）。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT pf_name, home_url FROM tp_server").fetchall()
    return {r["pf_name"]: (r["home_url"] or "") for r in rows}


def get_model_call_info(model_id):
    """调用模型所需的全部信息：模型名、接口地址来源（api_url / base_url）、计费单价。

    供 tp/llm.py 组装请求使用。找不到返回 None。
    """
    try:
        model_id = int(model_id)
    except (TypeError, ValueError):
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.id, m.model_name, m.in_price, m.out_price, "
            "       m.price_unit, m.price_unit_out, m.api_url, "
            "       s.pf_name AS platform, s.base_url "
            "FROM tp_model m JOIN tp_server s ON s.id = m.srv_id "
            "WHERE m.id = ?",
            (model_id,),
        ).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------
# prompt 表读写
# ------------------------------------------------------------
def _now_local() -> str:
    """本机系统时间（本地时区），格式对齐 SQLite DATETIME：YYYY-MM-DD HH:MM:SS。

    注意：SQLite 的 CURRENT_TIMESTAMP 返回 UTC，会比本地系统时间（如 GMT+8）少若干小时。
    「调试列表」的「时间」列要求按系统时间展示，故写入时显式使用本地时间，不依赖该默认值。
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_PROMPT_FIELDS = [
    "model_id", "prompt_name", "api_key", "sys_prompt",
    "user_prompt1", "user_prompt2", "user_prompt3",
    "user_prompt4", "user_prompt5", "user_prompt6",
    "u_promt_count",
]


def insert_prompt(data: dict) -> int:
    # created_at 显式写入本机系统时间（不依赖 SQLite CURRENT_TIMESTAMP 的 UTC 口径）
    fields = _PROMPT_FIELDS + ["created_at"]
    cols = ", ".join(f"`{c}`" for c in fields)
    ph = ", ".join("?" for _ in fields)
    vals = [data.get(c) for c in _PROMPT_FIELDS] + [_now_local()]
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO prompt ({cols}) VALUES ({ph})", vals)
        return cur.lastrowid


def update_prompt(pk: int, data: dict) -> int:
    """就地更新一条调试记录（对齐原型「同一条记录就地更新，新编辑则新增一条」）。

    不变更 id，故挂在同一 prompt_id 下的 evaluate 评估结果自然保留；
    created_at 随本次保存刷新（列表「时间」列 = 保存时的系统本地时间）。
    返回受影响行数；记录不存在时为 0（调用方据此回落为新增）。
    """
    fields = _PROMPT_FIELDS + ["created_at"]
    sets = ", ".join(f"`{c}` = ?" for c in fields)
    vals = [data.get(c) for c in _PROMPT_FIELDS] + [_now_local(), int(pk)]
    with get_conn() as conn:
        return conn.execute(f"UPDATE prompt SET {sets} WHERE id = ?", vals).rowcount


def list_prompts(keyword: str = ""):
    """调试列表：联表取 平台/官方名、模型名。按模型名模糊筛选。"""
    sql = (
        "SELECT p.id, p.prompt_name, p.sys_prompt, p.api_key, p.u_promt_count, "
        "       p.created_at, p.model_id, "
        "       m.model_name, s.pf_name AS server "
        "FROM prompt p "
        "LEFT JOIN tp_model m ON m.id = p.model_id "
        "LEFT JOIN tp_server s ON s.id = m.srv_id "
    )
    params = []
    key = (keyword or "").strip()
    if key:
        sql += "WHERE m.model_name LIKE ? "
        params.append(f"%{key}%")
    sql += "ORDER BY p.id DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_prompt(pk: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.*, m.model_name, s.pf_name AS server "
            "FROM prompt p "
            "LEFT JOIN tp_model m ON m.id = p.model_id "
            "LEFT JOIN tp_server s ON s.id = m.srv_id "
            "WHERE p.id = ?",
            (pk,),
        ).fetchone()
    return dict(row) if row else None


def delete_prompts(ids):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    with get_conn() as conn:
        # 评估结果显式清理：不依赖连接级 PRAGMA foreign_keys 的级联行为
        conn.execute(f"DELETE FROM evaluate WHERE prompt_id IN ({ph})", ids)
        return conn.execute(f"DELETE FROM prompt WHERE id IN ({ph})", ids).rowcount


# ------------------------------------------------------------
# evaluate 表读写（「评估」）
# ------------------------------------------------------------
_EVALUATE_FIELDS = ["prompt_id", "question", "answer", "m_answer", "is_true", "is_correct"]


def list_evaluations(prompt_id):
    """某条调试记录下的评估结果（按写入顺序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, prompt_id, question, answer, m_answer, is_true, is_correct "
            "FROM evaluate WHERE prompt_id = ? ORDER BY id ASC",
            (prompt_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_evaluations(prompt_id, rows) -> int:
    """评估结果整体覆盖写入：先清掉该 prompt_id 的旧结果，再逐条写入。

    不在此做业务/必填校验（原型的前端校验为准），只做字段取值。返回写入条数。
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM evaluate WHERE prompt_id = ?", (prompt_id,))
        cols = ", ".join(f"`{c}`" for c in _EVALUATE_FIELDS)
        ph = ", ".join("?" for _ in _EVALUATE_FIELDS)
        n = 0
        for r in rows or []:
            conn.execute(
                f"INSERT INTO evaluate ({cols}) VALUES ({ph})",
                (prompt_id, r.get("question"), r.get("answer"), r.get("m_answer"),
                 r.get("is_true"), r.get("is_correct")),
            )
            n += 1
        return n


def evaluate_stats_map():
    """prompt_id -> {total, judged, ok}；供调试列表「用例总数 / 准确率」列使用。

    total  = 该记录的评估用例条数
    judged = 已判定条数（is_true 非空，即非「待判定」）
    ok     = 判定正确条数（is_true = 1）
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT prompt_id, COUNT(*) AS total, "
            "       SUM(CASE WHEN is_true IS NOT NULL THEN 1 ELSE 0 END) AS judged, "
            "       SUM(CASE WHEN is_true = 1 THEN 1 ELSE 0 END) AS ok "
            "FROM evaluate WHERE prompt_id IS NOT NULL GROUP BY prompt_id"
        ).fetchall()
    return {
        r["prompt_id"]: {"total": r["total"], "judged": r["judged"] or 0, "ok": r["ok"] or 0}
        for r in rows
    }


def seed_prompts(force: bool = False) -> int:
    """写入示例调试记录 + 对应的评估结果（「调试列表」演示数据）。

    默认只在 prompt 表为空时写入，避免用户删空后刷新又"自动复活"；
    force=True 时先清空 prompt 表（evaluate 由外键 ON DELETE CASCADE 连带清空）再写入。
    返回实际写入的 prompt 条数。
    """
    with get_conn() as conn:
        if not force and conn.execute("SELECT COUNT(*) c FROM prompt").fetchone()["c"] > 0:
            return 0
        if force:
            conn.execute("DELETE FROM prompt")

        # 平台名 + 模型名 -> tp_model.id
        model_ids = {
            (r["pf_name"], r["model_name"]): r["id"]
            for r in conn.execute(
                "SELECT m.id, m.model_name, s.pf_name FROM tp_model m "
                "JOIN tp_server s ON s.id = m.srv_id"
            ).fetchall()
        }

        for p in SEED_PROMPTS:
            ups = (list(p["user_prompts"]) + [""] * 6)[:6]
            cur = conn.execute(
                "INSERT INTO prompt (model_id, prompt_name, api_key, sys_prompt, "
                "user_prompt1, user_prompt2, user_prompt3, "
                "user_prompt4, user_prompt5, user_prompt6, u_promt_count, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (model_ids.get((p["platform"], p["model"])), p["prompt_name"], "",
                 p["sys_prompt"], ups[0], ups[1], ups[2], ups[3], ups[4], ups[5],
                 len(p["user_prompts"]), p["created_at"]),
            )
            pid = cur.lastrowid

            # 该记录的「调试结果」→ evaluate 表（列表页 用例总数/准确率 的数据来源）
            for e in p.get("evals") or []:
                conn.execute(
                    "INSERT INTO evaluate (prompt_id, question, answer, m_answer, "
                    "is_true, is_correct) VALUES (?,?,?,?,?,?)",
                    (pid, e.get("question"), e.get("answer"), e.get("m_answer"),
                     e.get("is_true"), e.get("is_correct")),
                )
        return len(SEED_PROMPTS)


def prompt_to_snapshot(row: dict):
    """把 prompt 行转成前端 applySnapshot() 需要的初始状态。

    「调试结果」表的数据来自 evaluate 表：列表页点「评估」进入时，
    连同该条记录上次评估过的用例（问题 / 标准答案 / 模型答案 / 判定）一起回填。
    """
    rounds = [row.get(f"user_prompt{i}") or "" for i in range(2, 7)]
    rounds = [r for r in rounds if r.strip()]
    count = int(row.get("u_promt_count") or 0)

    cases = []
    for e in list_evaluations(row["id"]):
        t = e.get("is_true")
        if t is None:
            verdict = "待判定"
        else:
            verdict = "正确" if int(t) == 1 else "错误"
        cases.append({
            "question": e.get("question") or "",
            "answer": e.get("answer") or "",
            "result": e.get("m_answer") or "",
            "verdict": verdict,
            "corrected": int(e.get("is_correct") or 0) == 1,
            "inTokens": 0, "outTokens": 0, "costIn": 0, "costOut": 0,
        })

    return {
        "id": str(row["id"]),
        "name": row.get("prompt_name") or "",
        "server": row.get("server") or "",
        "modelId": row.get("model_id"),
        "model": row.get("model_name") or "",
        "apiKey": row.get("api_key") or "",
        "sysPrompt": row.get("sys_prompt") or "",
        "userPrompt": row.get("user_prompt1") or "",
        "multiTurn": count > 1,
        "rounds": rounds,
        "cases": cases,
    }


# ------------------------------------------------------------
# 表结构（「SQL / 表结构」页）
#   SQLite 的 information_schema 等价物 = sqlite_master + PRAGMA；
#   但 SQLite 不存储字段注释，故注释用下面这张对照表补齐（与建表语句口径一致）。
# ------------------------------------------------------------
DB_DISPLAY_NAME = "Prompt Test DB"
DB_DISPLAY_CN = "提示词测试主库"

TABLE_CN = {
    "tp_server": "服务表",
    "tp_model": "模型表",
    "prompt": "提示词表",
    "evaluate": "提示词评估表",
}

COMMENT_MAP = {
    ("tp_server", "id"): "主键ID",
    ("tp_server", "pf_name"): "平台/官方名",
    ("tp_server", "base_url"): "api网址(base_url)",
    ("tp_server", "comp_name"): "公司名",
    ("tp_server", "home_url"): "首页地址",
    ("tp_server", "created_at"): "创建时间",
    ("tp_server", "updated_at"): "更新时间",

    ("tp_model", "id"): "主键ID",
    ("tp_model", "srv_id"): "服务ID，关联 tp_server.id",
    ("tp_model", "model_name"): "模型名",
    ("tp_model", "in_price"): "输入token计费单价",
    ("tp_model", "out_price"): "输出token计费单价",
    ("tp_model", "price_unit"): "输入计费单位：K / 万 / 十万 / 百万",
    ("tp_model", "price_unit_out"): "输出计费单位：K / 万 / 十万 / 百万",
    ("tp_model", "model_type"): "模型类型，多个用英文逗号分隔",
    ("tp_model", "api_url"): "模型级接口地址，空则沿用服务器的 base_url",
    ("tp_model", "created_at"): "创建时间",
    ("tp_model", "updated_at"): "更新时间",

    ("prompt", "id"): "提示词标识",
    ("prompt", "model_id"): "模型信息（模型id），关联 tp_model.id",
    ("prompt", "prompt_name"): "模块名称",
    ("prompt", "api_key"): "调试时使用的key",
    ("prompt", "sys_prompt"): "系统提示词",
    ("prompt", "user_prompt1"): "用户提示词1",
    ("prompt", "user_prompt2"): "用户提示词2",
    ("prompt", "user_prompt3"): "用户提示词3",
    ("prompt", "user_prompt4"): "用户提示词4",
    ("prompt", "user_prompt5"): "用户提示词5",
    ("prompt", "user_prompt6"): "用户提示词6",
    ("prompt", "u_promt_count"): "轮数",
    ("prompt", "created_at"): "创建时间",

    ("evaluate", "id"): "唯一标识",
    ("evaluate", "prompt_id"): "评估提示词，关联 prompt.id，可获取评估提示词信息",
    ("evaluate", "question"): "问题",
    ("evaluate", "answer"): "标准答案",
    ("evaluate", "m_answer"): "模型答案",
    ("evaluate", "is_true"): "模型判断：1 为正确、0 为错误",
    ("evaluate", "is_correct"): "是否人为纠正：1 为人为纠正、0 为模型答案",
}


def _clean_default(v):
    """PRAGMA 的默认值带原始引号，按原型口径去掉外层引号。"""
    if v is None:
        return None
    s = str(v).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s


def _table_columns(conn, table: str, ddl: str):
    """单表字段清单 → [[字段名, 类型, 是否必填, 键, 默认值, 注释], ...]"""
    unique_cols = set()
    for idx in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        if not idx["unique"]:
            continue
        cols = [c["name"] for c in conn.execute(f'PRAGMA index_info("{idx["name"]}")').fetchall()
                if c["name"]]
        # 只认单列唯一索引；复合唯一（如 uk_srv_model）不逐列标 UK
        if len(cols) == 1:
            unique_cols.add(cols[0])

    fk_cols = {r["from"] for r in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()}
    auto_inc = "AUTOINCREMENT" in ddl.upper()

    cols = []
    for c in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        name = c["name"]
        is_pk = bool(c["pk"])
        required = bool(c["notnull"]) or is_pk
        key = "PK" if is_pk else ("UK" if name in unique_cols else ("FK" if name in fk_cols else ""))

        dflt = _clean_default(c["dflt_value"])
        if dflt is None:
            if is_pk and auto_inc:
                dflt = "AUTO_INCREMENT"
            else:
                dflt = "" if required else "NULL"

        cols.append([
            name,
            c["type"] or "",
            1 if required else 0,
            key,
            dflt,
            COMMENT_MAP.get((table, name), ""),
        ])
    return cols


def database_schema():
    """读真实库的表结构，结构对齐原型 SCHEMA / SERVER_DBS。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = [
            {
                "name": r["name"],
                "cn": TABLE_CN.get(r["name"], ""),
                "cols": _table_columns(conn, r["name"], r["sql"] or ""),
            }
            for r in rows
        ]
    return {"name": DB_DISPLAY_NAME, "cn": DB_DISPLAY_CN, "tables": tables}


def server_dbs_for_js():
    """「连接数据库」后左侧树用到的 库 → 表 → 字段，一次注入页面。"""
    return [database_schema()]


if __name__ == "__main__":
    init_db()
    print(f"数据库已就绪：{DB_PATH}")
    with get_conn() as conn:
        ev = conn.execute("SELECT COUNT(*) c FROM evaluate").fetchone()["c"]
    print(f"服务器 {len(servers_for_js())} 条 / 模型 {len(models_for_js())} 条 / "
          f"提示词 {len(list_prompts())} 条 / 评估 {ev} 条")
    print("表结构：", [(t["name"], len(t["cols"])) for t in database_schema()["tables"]])
