"""
xiniubot 搜索引擎 - 全局配置
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 爬虫 ──────────────────────────────────────────────
CRAWLER = {
    "user_agent": "xiniubot/1.0 (+https://xiniubot.com/bot)",
    "max_concurrent": 20,          # 最大并发请求数
    "max_depth": 5,                # 最大爬取深度
    "max_pages": 50000,            # 最大爬取页面数
    "timeout": 30,                 # 请求超时(秒)
    "politeness_delay": 1.0,       # 同域名最小间隔(秒)
    "max_retries": 3,
    "max_url_length": 2048,
    "respect_robots": True,
    "max_page_size": 5 * 1024 * 1024,  # 5 MB
}

# ── 存储 ──────────────────────────────────────────────
STORAGE = {
    "pages_dir": os.path.join(BASE_DIR, "data", "pages"),
    "index_dir": os.path.join(BASE_DIR, "data", "index"),
    "dict_dir": os.path.join(BASE_DIR, "data", "dict"),
}

# ── 分词 ──────────────────────────────────────────────
TOKENIZER = {
    "dict_path": os.path.join(BASE_DIR, "data", "dict", "dict.txt"),
    "stopwords_path": os.path.join(BASE_DIR, "data", "dict", "stopwords.txt"),
    "max_word_len": 8,
    "hmm_enabled": True,
}

# ── BM25 ──────────────────────────────────────────────
BM25_K1 = 1.5
BM25_B = 0.75
# ── 排序 (专业对齐) ──────────────────
RANKING = {
    "title_weight": 2.5,         # 标题字段加权
    "description_weight": 1.5,   # 描述字段加权
    "body_weight": 1.0,          # 正文字段基准权重
    "authority_weight": 0.35,    # 链接权威分融合系数 (PageRank)
    "time_decay_days": 0,        # 时间衰减窗口(天), 0 = 关闭
}

# ── 去重 (专业对齐) ──────────────────
DEDUP = {
    "enabled": True,             # 是否启用内容近似去重
    "simhash_threshold": 3,      # SimHash 海明距离阈值(<= 视为重复)
}

# ── URL 规范化 (专业对齐) ──────────────────
# 会被从 query 中剔除的追踪/统计参数
URL_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "spm", "source", "from", "share_token", "sns",
}

# ── 搜索服务 ──────────────────────────────────────────
SEARCH = {
    "page_size": 10,
    "snippet_chars": 200,
    "host": "0.0.0.0",
    "port": 5050,
}

# ── URL 过滤 ──────────────────────────────────────────
SKIP_EXT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".wav",
    ".exe", ".dmg", ".apk", ".ipa",
    ".css", ".js", ".json", ".xml", ".rss", ".woff", ".woff2", ".ttf",
}

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml"}

# ── 管理后台 ──────────────────────────────────────────
ADMIN = {
    "host": "127.0.0.1",
    "port": 8081,
    "password": "",        # 留空则首次启动自动生成随机密码
    "auth_enabled": True,  # 是否启用登录保护
}


# ── 运行时配置覆盖层 ─────────────────────────────────
# 管理后台会把可编辑配置写入 data/admin/config.json;
# 所有入口 (main.py / search_server.py / admin_server.py)
# 在导入本模块时自动加载该覆盖层, 使修改全局生效.
def _load_overlay():
    path = os.path.join(BASE_DIR, "data", "admin", "config.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for section, values in data.items():
        target = globals().get(section)
        if isinstance(target, dict) and isinstance(values, dict):
            for k, v in values.items():
                target[k] = v
        elif isinstance(values, list) and isinstance(target, set):
            target.update(values)
        else:
            # 模块级标量 (如 BM25_K1 / BM25_B)
            globals()[section] = values


_load_overlay()


# ── 搜索后端 (专业对齐) ─────────────────────────
# local = 自研 pickle 索引;  meili = Meilisearch (Docker/独立服务)
SEARCH_BACKEND = os.environ.get("XINIU_SEARCH_BACKEND", "local").strip().lower() or "local"

MEILI = {
    "host": os.environ.get("MEILI_HOST", "http://127.0.0.1:7700"),
    "api_key": os.environ.get("MEILI_MASTER_KEY", ""),
    "index_name": os.environ.get("MEILI_INDEX", "pages"),
    "meta_file": os.path.join(BASE_DIR, "data", "index", "meili_meta.pkl"),
    "max_docs_per_query": 2000,   # intitle/inurl 后过滤时的候选上限
}
