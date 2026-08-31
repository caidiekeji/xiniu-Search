#!/bin/sh
# xiniubot 单镜像容器入口
# 同时启动: 内置 Meilisearch(7700) + 管理后台(8081) + 搜索服务(5050)
set -e

mkdir -p /app/data/index /app/data/admin /app/data/pages /app/data/meili_data

# 单镜像默认使用内置 Meilisearch 后端 (未显式指定时)
export XINIU_SEARCH_BACKEND="${XINIU_SEARCH_BACKEND:-meili}"
export MEILI_HOST="${MEILI_HOST:-http://127.0.0.1:7700}"

# ── 1. 启动内置 Meilisearch ──
MEILI_DB=${MEILI_DB_PATH:-/app/data/meili_data}
MEILI_KEY=${MEILI_MASTER_KEY:-xiniubot-change-me-master-key}
echo "[xiniubot] 启动内置 Meilisearch (http://localhost:7700) ..."
/usr/local/bin/meilisearch \
  --db-path "$MEILI_DB" \
  --http-addr 0.0.0.0:7700 \
  --master-key "$MEILI_KEY" \
  --no-analytics &

# ── 2. 等待 Meilisearch 就绪 ──
echo "[xiniubot] 等待 Meilisearch 就绪..."
python - <<'PY'
import os, sys, time, urllib.request
base = os.environ.get("MEILI_HOST", "http://127.0.0.1:7700").rstrip("/")
url = base + "/health"
for i in range(60):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            if r.status == 200:
                print("[xiniubot] Meilisearch 就绪")
                sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
print("[xiniubot] 错误: Meilisearch 120 秒内未就绪", file=sys.stderr)
sys.exit(1)
PY

# ── 3. 启动管理后台 + 搜索服务 ──
echo "[xiniubot] 启动管理后台 (http://localhost:8081/admin)"
python admin_server.py &

echo "[xiniubot] 启动搜索服务 (http://localhost:5050)"
python search_server.py --port 5050
