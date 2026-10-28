# ============================================================
# Test Prompt 2.0 · 魔搭创空间（ModelScope Studio）Docker 镜像
#
# 关键约束（魔搭平台要求，官方文档口径）：
#   1. 服务必须监听 0.0.0.0:7860（端口固定；8080 被平台自身进程占用，禁用）
#   2. 仓库根目录需有本 Dockerfile，部署描述见同目录 ms_deploy.json
#   3. 默认文件系统每次重启清空，持久化目录为 /mnt/workspace（见 tp/db.py）
#   4. 代码里不允许硬编码密钥，一律通过「环境变量 / Secrets」注入
#
# 注意：Django 6.1 要求 Python >= 3.12，所以基础镜像不能用常见的 python:3.10。
# ============================================================
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

# 与魔搭官方 Docker 示例一致的工作目录
WORKDIR /home/user/app

# 先只拷依赖清单，让依赖层可被缓存复用
COPY requirements.txt ./requirements.txt

# 国内构建优先走阿里云 PyPI 镜像；镜像不可用时自动回落官方源，避免构建直接失败
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ \
 || pip install --no-cache-dir -r requirements.txt

# 再拷项目代码
COPY . .

# 启动脚本需要可执行权限（Windows 上复制过来的文件默认没有）
RUN chmod +x /home/user/app/docker/entrypoint.sh

# 魔搭创空间对外只转发 7860
EXPOSE 7860

ENTRYPOINT ["/bin/sh", "/home/user/app/docker/entrypoint.sh"]
