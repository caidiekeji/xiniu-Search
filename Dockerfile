# xiniubot 搜索引擎 - 单镜像 (内置 Meilisearch)
# 一个镜像 = 完整系统: 内置 meilisearch(7700) + 搜索服务(5050) + 管理后台(8081)
# 多架构构建: buildx 自动注入 TARGETARCH (amd64 / arm64)
FROM python:3.12-slim

ARG TARGETARCH
ARG MEILI_VERSION=1.53.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# 1. 下载并安装 Meilisearch 社区版静态二进制 (按架构: amd64 / arm64 -> aarch64)
RUN set -eux; \
    if [ "$TARGETARCH" = "arm64" ]; then MEILI_ARCH=aarch64; else MEILI_ARCH=amd64; fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/meilisearch/meilisearch/releases/download/v${MEILI_VERSION}/meilisearch-linux-${MEILI_ARCH}" -o /usr/local/bin/meilisearch \
    && chmod +x /usr/local/bin/meilisearch \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

# 2. 安装 Python 依赖 (利用构建缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 拷贝项目代码 (运行时数据由卷挂载到 /app/data)
COPY . .

# 单镜像: 内置 meili(7700) + 搜索服务(5050) + 管理后台(8081)
EXPOSE 5050 8081 7700
CMD ["sh", "/app/entrypoint.sh"]
