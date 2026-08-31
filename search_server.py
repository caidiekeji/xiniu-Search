#!/usr/bin/env python3
"""
xiniubot 搜索 Web 服务
======================
启动方式:
  python search_server.py
  python search_server.py --port 5050

访问: http://localhost:5050
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import OrderedDict

from flask import Flask, request, render_template_string
import config
from search.backends import create_backend

app = Flask(__name__)
engine = None
_index_mtime = 0.0


class _LRU:
    """简易 LRU 缓存 (查询结果)."""
    def __init__(self, maxsize=256):
        self.maxsize = maxsize
        self._d = OrderedDict()
    def get(self, key):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None
    def put(self, key, value):
        if key in self._d:
            self._d.move_to_end(key)
        self._d[key] = value
        if len(self._d) > self.maxsize:
            self._d.popitem(last=False)
    def clear(self):
        self._d.clear()


_cache = _LRU()


def get_engine():
    """惰性初始化搜索引擎.

    - local 模式: 磁盘索引文件变化时自动重载 (兼容 flask run)
    - meili 模式: 惰性单例 (数据在 Meilisearch 内, 无需重载)
    """
    global engine, _index_mtime
    if config.SEARCH_BACKEND == "meili":
        if engine is None:
            engine = create_backend()
            _cache.clear()
        return engine
    idx_file = os.path.join(config.STORAGE["index_dir"], "inverted_index.pkl")
    mt = os.path.getmtime(idx_file) if os.path.isfile(idx_file) else 0
    if engine is None or mt != _index_mtime:
        engine = create_backend()
        _index_mtime = mt
        _cache.clear()
    return engine


# ═══════════════════════════════════════════════════════
#  HTML 模板
# ═══════════════════════════════════════════════════════

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>xiniubot 搜索引擎</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap');

  :root {
    --bg: #0a0a0c;
    --surface: #111114;
    --surface-2: #18181c;
    --border: #25252a;
    --accent: #f0b429;
    --accent-dim: #c4921e;
    --text: #e8e6e3;
    --text-muted: #6b6a68;
    --text-dim: #44444a;
    --link: #6ea8fe;
    --link-visited: #a78bfa;
    --danger: #ef4444;
    --success: #22c55e;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Noto Sans SC', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  /* ── 首页居中 ── */
  .home-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 2rem;
  }

  .home-logo {
    font-family: 'JetBrains Mono', monospace;
    font-size: 3.2rem;
    font-weight: 600;
    letter-spacing: -0.04em;
    margin-bottom: 0.3rem;
    background: linear-gradient(135deg, var(--accent) 0%, #f5d77a 50%, var(--accent-dim) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .home-subtitle {
    color: var(--text-muted);
    font-size: 0.85rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-bottom: 2.5rem;
  }

  /* ── 搜索框 ── */
  .search-box {
    width: 100%;
    max-width: 640px;
    position: relative;
  }

  .search-box input[type="text"] {
    width: 100%;
    padding: 1rem 1.4rem;
    padding-right: 3.5rem;
    font-size: 1.05rem;
    font-family: 'Noto Sans SC', sans-serif;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--text);
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
  }

  .search-box input[type="text"]:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(240, 180, 41, 0.12);
  }

  .search-box input[type="text"]::placeholder {
    color: var(--text-dim);
  }

  .search-box button {
    position: absolute;
    right: 0.5rem;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.5rem;
    color: var(--accent);
    font-size: 1.3rem;
    transition: transform 0.15s;
  }

  .search-box button:hover {
    transform: translateY(-50%) scale(1.1);
  }

  /* ── 统计信息 ── */
  .stats-bar {
    margin-top: 2rem;
    font-size: 0.78rem;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
  }

  .stats-bar span {
    margin: 0 0.6rem;
  }

  /* ── 结果页 ── */
  .results-wrapper {
    max-width: 800px;
    margin: 0 auto;
    padding: 1.5rem 2rem;
  }

  .results-header {
    display: flex;
    align-items: center;
    gap: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
  }

  .results-header .logo-sm {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    font-size: 1.2rem;
    color: var(--accent);
    text-decoration: none;
    white-space: nowrap;
  }

  .results-header .search-box {
    flex: 1;
  }

  .results-header .search-box input {
    padding: 0.7rem 1rem;
    font-size: 0.95rem;
  }

  .results-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-bottom: 1.2rem;
  }

  /* ── 结果卡片 ── */
  .result-item {
    margin-bottom: 1.8rem;
    animation: fadeUp 0.3s ease forwards;
    opacity: 0;
  }

  .result-item:nth-child(1) { animation-delay: 0.05s; }
  .result-item:nth-child(2) { animation-delay: 0.1s; }
  .result-item:nth-child(3) { animation-delay: 0.15s; }
  .result-item:nth-child(4) { animation-delay: 0.2s; }
  .result-item:nth-child(5) { animation-delay: 0.25s; }
  .result-item:nth-child(6) { animation-delay: 0.3s; }
  .result-item:nth-child(7) { animation-delay: 0.35s; }
  .result-item:nth-child(8) { animation-delay: 0.4s; }
  .result-item:nth-child(9) { animation-delay: 0.45s; }
  .result-item:nth-child(10) { animation-delay: 0.5s; }

  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .result-url {
    font-size: 0.78rem;
    color: var(--success);
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 0.25rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .result-title {
    font-size: 1.15rem;
    font-weight: 500;
    margin-bottom: 0.3rem;
  }

  .result-title a {
    color: var(--link);
    text-decoration: none;
    transition: color 0.15s;
  }

  .result-title a:visited {
    color: var(--link-visited);
  }

  .result-title a:hover {
    text-decoration: underline;
  }

  .result-snippet {
    font-size: 0.88rem;
    color: var(--text-muted);
    line-height: 1.65;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .result-snippet b {
    color: var(--accent);
    font-weight: 500;
    background: rgba(240, 180, 41, 0.08);
    padding: 0 2px;
    border-radius: 2px;
  }

  .result-score {
    font-size: 0.7rem;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
    margin-top: 0.3rem;
  }

  /* ── 分页 ── */
  .pagination {
    display: flex;
    justify-content: center;
    gap: 0.5rem;
    margin-top: 2rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
  }

  .pagination a, .pagination span {
    padding: 0.5rem 0.9rem;
    font-size: 0.85rem;
    border-radius: 6px;
    text-decoration: none;
    font-family: 'JetBrains Mono', monospace;
    transition: all 0.15s;
  }

  .pagination a {
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
  }

  .pagination a:hover {
    background: var(--surface-2);
    border-color: var(--accent);
    color: var(--accent);
  }

  .pagination .current {
    background: var(--accent);
    color: var(--bg);
    font-weight: 600;
    border: 1px solid var(--accent);
  }

  /* ── 空结果 ── */
  .no-results {
    text-align: center;
    padding: 4rem 0;
    color: var(--text-muted);
  }

  .no-results h2 {
    font-size: 1.3rem;
    margin-bottom: 0.5rem;
    color: var(--text);
  }

  /* ── 底部 ── */
  .footer {
    text-align: center;
    padding: 2rem;
    font-size: 0.72rem;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
  }

  /* —— 建议下拉 / 纠错提示 / 高级语法 —— */
  .suggest {
    position: absolute; top: calc(100% + 6px); left: 0; right: 0;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; z-index: 50; display: none; overflow: hidden;
    box-shadow: 0 8px 24px rgba(0,0,0,.4);
  }
  .suggest-item { padding: .65rem 1.1rem; font-size: .9rem; color: var(--text); cursor: pointer; }
  .suggest-item:hover { background: var(--surface-2); color: var(--accent); }
  .did-you-mean { margin-bottom: .5rem; color: var(--text-muted); font-size: .85rem; }
  .did-you-mean a { color: var(--accent); text-decoration: none; }
  .did-you-mean a:hover { text-decoration: underline; }
  .advanced-hint { margin-top: 1rem; font-size: .72rem; color: var(--text-dim); font-family: 'JetBrains Mono', monospace; }
</style>
</head>
<body>

{% if not query %}
<!-- ═══ 首页 ═══ -->
<div class="home-wrapper">
  <div class="home-logo">xiniubot</div>
  <div class="home-subtitle">全网中文搜索引擎</div>
  <div class="search-box">
    <form action="/search" method="get">
      <input type="text" name="q" id="q" placeholder="输入关键词搜索..." autofocus autocomplete="off">
      <button type="submit">&#8594;</button>
    </form>
    <div class="suggest" id="suggest-box"></div>
  </div>
  <div class="advanced-hint">高级语法: "精确短语" &middot; -排除 &middot; OR &middot; site:域名 &middot; intitle:词 &middot; inurl:串</div>
  <div class="stats-bar">
    <span>索引 {{ stats.total_docs }} 篇文档</span>
    <span>|</span>
    <span>词典 {{ stats.vocabulary_size }} 个词项</span>
  </div>
</div>

{% else %}
<!-- ═══ 结果页 ═══ -->
<div class="results-wrapper">
  <div class="results-header">
    <a class="logo-sm" href="/">xiniubot</a>
    <div class="search-box">
      <form action="/search" method="get">
        <input type="text" name="q" id="q" value="{{ query }}" autocomplete="off">
        <button type="submit">&#8594;</button>
      </form>
      <div class="suggest" id="suggest-box"></div>
    </div>
  </div>

  <div class="results-meta">
    {% if suggestion %}<div class="did-you-mean">您是不是想找：<a href="/search?q={{ suggestion }}">{{ suggestion }}</a></div>{% endif %}
    找到约 {{ total }} 条结果, 用时 {{ elapsed }} 秒
  </div>

  {% if results %}
    {% for r in results %}
    <div class="result-item">
      <div class="result-url">{{ r.url }}</div>
      <div class="result-title">
        <a href="{{ r.url }}" target="_blank">{{ r.title }}</a>
      </div>
      <div class="result-snippet">{{ r.snippet|safe }}</div>
      <div class="result-score">得分: {{ "%.4f"|format(r.score) }} | {{ r.word_count }} 词</div>
    </div>
    {% endfor %}

    {% if total_pages > 1 %}
    <div class="pagination">
      {% if page > 1 %}
        <a href="/search?q={{ query }}&page={{ page - 1 }}">&#8592; 上一页</a>
      {% endif %}

      {% for p in page_range %}
        {% if p == page %}
          <span class="current">{{ p }}</span>
        {% else %}
          <a href="/search?q={{ query }}&page={{ p }}">{{ p }}</a>
        {% endif %}
      {% endfor %}

      {% if page < total_pages %}
        <a href="/search?q={{ query }}&page={{ page + 1 }}">下一页 &#8594;</a>
      {% endif %}
    </div>
    {% endif %}

  {% else %}
    <div class="no-results">
      <h2>未找到相关结果</h2>
      <p>请尝试其他关键词</p>
    </div>
  {% endif %}
</div>
{% endif %}

<div class="footer">xiniubot search engine &copy; 2024</div>

<script>
(function(){
  function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  var q = document.getElementById('q');
  var box = document.getElementById('suggest-box');
  if (!q || !box) return;
  var timer = null;
  function hide(){ box.style.display = 'none'; box.innerHTML = ''; }
  q.addEventListener('input', function(){
    clearTimeout(timer);
    var v = q.value.trim();
    if (v.length < 1) { hide(); return; }
    timer = setTimeout(function(){
      fetch('/api/suggest?prefix=' + encodeURIComponent(v))
        .then(function(r){ return r.json(); })
        .then(function(d){
          var list = (d && d.suggestions) || [];
          if (!list.length) { hide(); return; }
          box.innerHTML = list.map(function(s){ return '<div class="suggest-item">' + esc(s) + '</div>'; }).join('');
          box.style.display = 'block';
          Array.prototype.forEach.call(box.children, function(el){
            el.addEventListener('mousedown', function(e){ e.preventDefault(); q.value = el.textContent; hide(); });
          });
        })
        .catch(hide);
    }, 150);
  });
  document.addEventListener('click', function(e){
    if (e.target !== q && e.target !== box) hide();
  });
})();
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════
#  Flask 路由
# ═══════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        results=None,
        query="",
        suggestion="",
        stats=get_engine().index.stats(),
    )


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    page = _safe_page(request.args.get("page"))

    if not query:
        return render_template_string(
            HTML_TEMPLATE,
            results=None,
            query="",
            stats=get_engine().index.stats(),
        )

    start = time.time()
    cache_key = ("search", query, page)
    cached = _cache.get(cache_key)
    if cached is not None:
        results, total, elapsed = cached
    else:
        results, total = get_engine().search(query, page=page)
        elapsed = time.time() - start
        _cache.put(cache_key, (results, total, elapsed))

    # 无结果时尝试拼写纠错 (did-you-mean)
    suggestion = ""
    if total == 0:
        suggestion = get_engine().correct(query) or ""

    page_size = config.SEARCH["page_size"]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    # 分页范围 (显示当前页附近的页码)
    page_start = max(1, page - 4)
    page_end = min(total_pages, page + 5)
    page_range = range(page_start, page_end + 1)

    return render_template_string(
        HTML_TEMPLATE,
        results=results,
        query=query,
        total=total,
        elapsed=f"{elapsed:.3f}",
        page=page,
        total_pages=total_pages,
        page_range=page_range,
        suggestion=suggestion,
        stats=get_engine().index.stats(),
    )


@app.route("/api/search")
def api_search():
    """JSON API 接口."""
    from flask import jsonify
    query = request.args.get("q", "").strip()
    page = _safe_page(request.args.get("page"))
    try:
        page_size = min(50, max(1, int(request.args.get("page_size", 10))))
    except (TypeError, ValueError):
        page_size = 10

    if not query:
        return jsonify({"error": "empty query", "results": [], "total": 0})

    cache_key = ("api", query, page, page_size)
    cached = _cache.get(cache_key)
    if cached is not None:
        results, total = cached
    else:
        results, total = get_engine().search(query, page=page, page_size=page_size)
        _cache.put(cache_key, (results, total))
    suggestion = get_engine().correct(query) if total == 0 else ""

    return jsonify({
        "query": query,
        "total": total,
        "page": page,
        "page_size": page_size,
        "suggestion": suggestion or None,
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "score": round(r.score, 4),
                "word_count": r.word_count,
            }
            for r in results
        ],
    })


@app.route("/api/suggest")
def api_suggest():
    """搜索建议 (autocomplete)."""
    from flask import jsonify
    prefix = request.args.get("prefix", "").strip()
    return jsonify({"prefix": prefix, "suggestions": get_engine().suggest(prefix)})


@app.route("/api/correct")
def api_correct():
    """拼写纠错 (did-you-mean)."""
    from flask import jsonify
    q = request.args.get("q", "").strip()
    return jsonify({"query": q, "suggestion": get_engine().correct(q) or None})


@app.route("/api/stats")
def api_stats():
    """索引统计 API."""
    from flask import jsonify
    return jsonify(get_engine().index.stats())


# ═══════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════

def _safe_page(raw) -> int:
    """安全解析页码参数, 非法输入默认第 1 页."""
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def main():
    global engine

    parser = argparse.ArgumentParser(description="xiniubot 搜索 Web 服务")
    parser.add_argument("--host", default=config.SEARCH["host"])
    parser.add_argument("--port", type=int, default=config.SEARCH["port"])
    args = parser.parse_args()

    # 初始化搜索引擎 (加载已有索引)
    engine = get_engine()

    stats = engine.index.stats()
    print("=" * 50)
    print("  xiniubot 搜索引擎")
    print("=" * 50)
    print(f"  文档数: {stats['total_docs']}")
    print(f"  词典大小: {stats['vocabulary_size']}")
    print(f"  平均文档长度: {stats['avg_doc_length']}")
    print(f"  访问: http://localhost:{args.port}")
    print("=" * 50)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
