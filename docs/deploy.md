# 部署说明

本项目是一个需要 Python 运行时 + SQLite 写入的 Django 应用，所以能落地的三处环境如下。

| 环境 | 适用 | 关键约束 |
| --- | --- | --- |
| 本地 / 自有服务器 | 开发、内网使用 | 需要 Python ≥ 3.12 |
| Docker | 任意容器平台（含魔搭创空间） | 必须监听 `0.0.0.0:7860` 时才能对上魔搭 |
| GitHub | **只做源码托管 + CI**，不能跑服务 | Pages 只托管静态文件 |

---

## 一、本地 / 自有服务器

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/smoke_check.py                        # 自检，全 PASS 再往下
TP_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 --worker-class gthread --workers 1 --threads 8 --timeout 300
```

要点：

- **`--timeout` 不要低于 300**：批量调试是逐条真实调用大模型，一批可能跑过 120 秒。
- `--workers 1` 是刻意的：SQLite 多进程写会锁库，用 1 worker × 8 threads 换并发。
- 生产必须设 `TP_SECRET_KEY`，否则用的是仓库里的占位值。
- 数据库文件默认在项目根；想换位置就设 `TP_DB_PATH`。

---

## 二、Docker

```bash
docker build -t test-prompt:2.0 .
docker run --rm -p 7860:7860 -e TP_SECRET_KEY=<随机长串> test-prompt:2.0
```

镜像里做的事（`docker/entrypoint.sh`）：

1. 探测 `/mnt/workspace` 是否可写，可写则把数据库放那儿（重启不丢），否则回落容器内并打警告；
2. `db.init_db()` 建表 + 灌平台/模型种子数据，`db.seed_prompts()` 灌示例记录（都幂等，不会重复插）；
3. `gunicorn` 监听 `0.0.0.0:7860`。

把数据库挂出来（本地想保留数据时）：

```bash
docker run --rm -p 7860:7860 -v "$PWD/data:/data" -e TP_DB_PATH=/data/Prompt_Test_DB.db test-prompt:2.0
```

---

## 三、魔搭创空间（ModelScope Studio）

用仓库根的 `Dockerfile` + `ms_deploy.json`，类型选 **Docker**。

| 项 | 要求 |
| --- | --- |
| 账号 | 已实名认证，且绑定阿里云账号并完成云账号实名（Docker 类型的硬性门槛） |
| 端口 | 必须 `0.0.0.0:7860`；**不能改成 8080**（平台自身进程占用） |
| 入口 | 仓库根目录要有 `Dockerfile` 和 `ms_deploy.json` |
| 持久化 | 只有 `/mnt/workspace` 会保留；**迁移或重命名创空间同样会丢数据** |
| 密钥 | 平台禁止代码内硬编码，Key 走环境变量：`TP_SF_API_KEY`、`TP_JUDGE_API_KEY`、`TP_SECRET_KEY` |
| Git | 默认分支 `master`，禁止 force push；单文件 > 100MB 需 LFS |

网页端创建：创空间 → 创建 → 编程式创空间 → 快速部署并创建 → 上传项目 → 确认识别为 Docker / 7860 / 2vCPU-16G。

部署后按这个清单验收：默认页跳到「提示词调试」、选模型弹窗能列出平台与模型（说明建表成功）、保存后能跳到列表、列表里能看到 6 条示例记录、「SQL / 表结构」能提取出 4 张表。

---

## 四、GitHub 上的定位

`github.com` 托管的是**源码**，不跑 Django：

- **Pages 不行**：Pages 只有静态文件，没有 Python 运行时，也没有可写的 SQLite。
- **CI 可以**：`.github/workflows/ci.yml` 在每次 push 时跑两件事 —— 冒烟检查（建表 + 7 个页面 + 写入链路 + 表结构内省）、`docker build` 并在容器里 curl 验证服务真的起来了。
- 想要「点开就能用」的链接，两条路：把 Docker 镜像部署到任意容器平台；或把 1.0 静态原型放上 Pages。

首次推送后，仓库 Settings → Actions → General 确认 Workflow permissions 允许读取，
Actions 页面出现绿色勾即代表「克隆下来就能起」。

---

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 构建报 `Django requires python>=3.12` | 基础镜像被换成了 3.10/3.11，改回 `python:3.13-slim` |
| 容器起来但页面打不开 | 服务没监听 `0.0.0.0:7860`，检查 entrypoint 的 `--bind` |
| 重启后数据没了 | 数据库没落到 `/mnt/workspace`，看启动日志里的告警 |
| 页面 500 看不到原因 | 环境变量加 `TP_DEBUG=1` 重跑，看运行日志 |
| 「浏览…」点了报错 | 云端无桌面环境，属预期；改为直接填绝对路径 |
| 批量调试请求超时 | 调大 gunicorn `--timeout`，或前端把每批条数调小 |
| pip 拉包慢 / 超时 | Dockerfile 已优先走阿里云镜像，失败自动回落官方源 |
