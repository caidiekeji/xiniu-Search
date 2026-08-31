#!/bin/sh
# xiniubot 应用容器入口
# 启动: 管理后台(8081) + 搜索服务(5050)
set -e

mkdir -p /app/data/index /app/data/admin /app/data/pages

if [ "$XINIU_SEARCH_BACKEND" = "meili" ]; then
  echo "[xiniubot] 后端=meili, 等待 Meilisearch 就绪..."
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
print("[xiniubot] 错误: Meilisearch 120 秒内未就绪, 请检查 meilisearch 容器", file=sys.stderr)
sys.exit(1)
PY
fi

echo "[xiniubot] 启动管理后台 (http://localhost:8081/admin)"
python admin_server.py &

echo "[xiniubot] 启动搜索服务 (http://localhost:5050)"
python search_server.py --port 5050
