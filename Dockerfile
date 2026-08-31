# xiniubot 搜索引擎 - 应用镜像
# 基于 Docker 构建 (GitHub Actions 自动打包并推送至 ghcr.io)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# 先装依赖, 利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目代码 (运行时数据由 docker-compose 卷挂载到 /app/data)
COPY . .

# 容器入口: 启动 搜索服务(5050) + 管理后台(8081)
# 当 XINIU_SEARCH_BACKEND=meili 时, 入口会先等待 Meilisearch 就绪
EXPOSE 5050 8081
CMD ["sh", "/app/entrypoint.sh"]
