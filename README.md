# Test Prompt · 大模型提示词调试管理台

一个能真正跑起来的「提示词调试」小工具：**选模型 → 写系统/用户提示词 → 真实调用 → 保存入库 → 回列表评估**。

面向的场景很具体 —— 手上有十几家模型服务商、几十个模型，每次调 prompt 都要临时拼接口、抄 token 用量、手动记「这次改完到底是变好还是变坏」。这个工具把这些动作收进一个后台：模型与计费一处维护，调试记录可留存，评估结果可回溯。

> 2.0 = 可运行版本（Django + SQLite）。1.0 是同名项目的静态原型，只验证信息架构与字段规则，交互为前端模拟。

## 功能

**提示词调试**
- 从数据库读平台与模型清单（13 个平台 / 30 个模型），选模型自动带出输入/输出计费
- 系统提示词 + 用户提示词 1~6，支持多轮对话
- 单次调试**真实调用**所选模型接口，回传回答、token 用量、耗时
- 「调试结果」支持 Excel(.xlsx) 用例导入、批量调试（逐条生成模型答案 + 独立判定模型判对错）、人工纠正
- 保存写入 `prompt` 表并跳转列表；「评估」把判定结果写入 `evaluate` 表

**调试列表**
- 全部记录，系统提示词超 20 字截断（悬停看全文）
- 按模型名搜索、表头全选、勾选批量删除
- 「编辑」回填调试页；「评估」跳回调试页并定位到「调试结果」区域
- 用例总数 / 准确率直接读 `evaluate` 表统计

**SQL / 表结构**
- 「服务器 → 数据库 → 表」三级树，勾选提取字段明细，可一键复制
- 真实连接 **MySQL / MariaDB / PostgreSQL / SQLite**，表结构来自数据库内省而非演示数据
- 库里没有注释的表/字段，可由 AI 补充「表结构说明」；表关系以真实外键为准，覆盖不全时 AI 兜底推断

## 技术栈

| 项 | 说明 |
| --- | --- |
| 语言 | Python 3.13（Django 6.1 要求 ≥ 3.12） |
| 框架 | Django 6.1（模板 + 原生 JS，无前端构建） |
| 服务 | 本地 `runserver`；生产 `gunicorn`（gthread，`0.0.0.0:7860`） |
| 数据库 | SQLite（`tp/db.py` 直连，不用 ORM 管业务表） |
| 依赖 | Django、gunicorn、pymysql、pg8000（全部纯 Python，无编译依赖） |

## 快速开始

```bash
git clone <本仓库地址>
cd test-prompt
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver 8000
```

打开 http://127.0.0.1:8000 ，首次访问自动建表并写入示例数据（13 平台 / 30 模型 / 6 条调试记录 / 8 条评估），**不配任何 Key 也能把整套流程点一遍**。

提交前先自检（CI 也是跑这个脚本）：

```bash
python scripts/smoke_check.py
```

用 Docker 预演生产环境：

```bash
docker build -t test-prompt:2.0 .
docker run --rm -p 7860:7860 test-prompt:2.0
# http://localhost:7860
```

## 目录结构

```
manage.py               Django 入口
config/                 settings / urls / wsgi（密钥与 DEBUG 全走环境变量）
tp/
  db.py                 数据访问层：建表、种子数据、prompt/evaluate 读写、表结构内省
  views.py              页面渲染 + 保存/删除/评估接口
  views_llm.py          单次调试 / 批量调试（真实调用 + 并发 + 判定）
  views_db.py           连接数据库、生成表结构说明
  views_import.py       Excel 用例导入
  views_fs.py           服务器端原生文件对话框（仅本机可用，云端降级提示）
  llm.py                模型调用封装（OpenAI 兼容协议）
  sql_conn.py           MySQL / PostgreSQL / SQLite 内省
  schema_desc.py        表结构说明生成
  xlsx.py               .xlsx 解析（zipfile + ElementTree，无第三方依赖）
templates/              三个页面模板（与 1.0 原型的样式/字段一致）
prototypes/             尚未开发的原型页，原样返回，保证菜单不 404
scripts/smoke_check.py  冒烟检查（CI 用）
docs/                   部署说明
.github/workflows/      CI：冒烟检查 + Docker 构建验证
```

## 数据表

| 表 | 说明 |
| --- | --- |
| `tp_server` | 服务器 / 平台：平台名（唯一）、base_url、公司名、官网 |
| `tp_model` | 模型：归属服务器（FK）、模型名、输入/输出计费与单位、类型、接口地址 |
| `prompt` | 调试记录：`id / model_id / prompt_name / api_key / sys_prompt / user_prompt1~6 / u_promt_count` |
| `evaluate` | 评估结果：`id / prompt_id / question / answer / m_answer / is_true / is_correct` |

模型表对 `(srv_id, model_name)` 建唯一索引；计费单价按「元 / 百万 tokens」口径存储，展示时按单位换算。

## 环境变量

全部可选，密钥一律不写进代码。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TP_SF_API_KEY` | 空 | 硅基流动 Key，「表结构说明」AI 补备注用 |
| `TP_SF_BASE_URL` | `https://api.siliconflow.cn/v1` | 硅基流动接口地址 |
| `TP_SF_MODEL` | `deepseek-ai/DeepSeek-V4-Pro` | 生成表结构说明的模型 |
| `TP_JUDGE_API_KEY` | 空 | 判定模型 Key（不配则批量调试只出答案、不自动判定） |
| `TP_JUDGE_MODEL` | `deepseek-ai/DeepSeek-V4-Pro` | 判定模型 |
| `TP_JUDGE_BASE_URL` | `https://api.siliconflow.cn/v1` | 判定模型接口地址 |
| `TP_SECRET_KEY` | 内置占位值 | Django 生产密钥，部署时务必替换 |
| `TP_DEBUG` | `0` | 设为 `1` 打开调试页 |
| `TP_ALLOWED_HOSTS` | `*` | 逗号分隔 |
| `TP_DB_PATH` | `/mnt/workspace/Prompt_Test_DB.db`（存在则用），否则项目根 | 数据库文件路径 |

## 部署

三种方式见 [docs/deploy.md](docs/deploy.md)：本地/自有服务器、Docker、魔搭创空间。

一句话版本：

```bash
TP_SECRET_KEY=<随机长串> gunicorn config.wsgi:application \
  --bind 0.0.0.0:7860 --worker-class gthread --workers 1 --threads 8 --timeout 300
```

### 为什么没有 GitHub Pages

Django 需要 Python 运行时和 SQLite 写入，**GitHub Pages 只能托管静态文件**，跑不了本项目。
本仓库在 GitHub 上的用途是**源码托管 + CI 验证**：每次 push 会跑冒烟检查并构建 Docker 镜像，确认「克隆下来就能起」。
需要点击即用的在线 Demo，请走 Docker 平台，或使用 1.0 静态原型版（纯 HTML，可以上 Pages）。

## 已知限制

- 「表结构」页 SQLite 的「浏览… / 创建…」依赖**服务器端**弹出系统文件对话框，仅本机运行可用；云端容器没有桌面环境，请直接在输入框填绝对路径。
- 容器无法访问你本机的数据库，只能连容器网络可达的地址。
- 调试页填的 `api_key` 会落到 `prompt.api_key` 字段，公开部署建议留空、演示时现场输入。
- SQLite 无并发写优化，单人/小团队够用；多人同时写要换 PostgreSQL。

## License

未附 License 文件。如需开源，建议补一个（Apache-2.0 或 MIT 均可）。
