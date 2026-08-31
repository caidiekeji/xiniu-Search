# -*- coding: utf-8 -*-
"""
xiniubot 管理后台服务
=====================
专业搜索引擎管理控制台, 提供:
  - 仪表盘: 运行状态 / 爬取进度 / 索引统计 / 最近抓取
  - 爬虫控制: 网页启动 / 停止爬虫
  - 配置管理: 可视化修改爬虫 / 搜索 / 分词 / 排序 / URL 过滤参数
  - 种子管理: 添加 / 批量 / 删除种子 URL
  - 队列监控: 待爬队列 / 域名分布 / 去重规模
  - 索引管理: 文档检索 / 浏览 / 删除
  - 任务历史: 每次爬取任务的起止 / 结果 / 参数
  - 日志查看: 爬虫实时文件日志

启动:
  python admin_server.py [--host 127.0.0.1] [--port 8081]

访问: http://127.0.0.1:8081/admin/
"""

import argparse
import json
import logging
import os
import secrets
import sys
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)

import config
import admin_config
from admin_runner import manager
from search.backends import create_backend

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True

logging.getLogger("werkzeug").setLevel(logging.WARNING)

DATA_DIR = os.path.join(config.BASE_DIR, "data", "admin")
SEEDS_FILE = os.path.join(DATA_DIR, "seeds.json")
INDEX_FILE = os.path.join(config.STORAGE["index_dir"], "inverted_index.pkl")


# ── 会话密钥 (持久化, 重启不失效) ────────────────────
def _ensure_secret() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, "secret.key")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except Exception:
            pass
    v = secrets.token_hex(32)
    with open(p, "w", encoding="utf-8") as f:
        f.write(v)
    return v


app.secret_key = _ensure_secret()


# ── 认证 ──────────────────────────────────────────────
def _get_password() -> str:
    pwd = config.ADMIN.get("password", "") or ""
    if pwd:
        return pwd
    p = os.path.join(DATA_DIR, "admin_password.txt")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except Exception:
            pass
    pwd = secrets.token_urlsafe(9)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(pwd)
    print("=" * 52)
    print("  管理后台初始密码: %s" % pwd)
    print("  已保存至: %s" % p)
    print("=" * 52)
    return pwd


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if config.ADMIN.get("auth_enabled", True) and not session.get("admin"):
            if request.path.startswith("/admin/api") or request.is_json:
                return jsonify({"ok": False, "error": "未登录"}), 401
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def csrf_required(fn):
    """要求写操作携带 CSRF token (会话内令牌)."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if config.ADMIN.get("auth_enabled", True):
            expected = session.get("csrf", "")
            got = request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected, got or ""):
                return jsonify({"ok": False, "error": "CSRF 校验失败, 请刷新页面重试"}), 403
        return fn(*args, **kwargs)
    return wrapper


# ── 种子存储 ──────────────────────────────────────────
def _load_seeds() -> list:
    if not os.path.isfile(SEEDS_FILE):
        return []
    try:
        with open(SEEDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, str) and x.strip()] if isinstance(data, list) else []
    except Exception:
        return []


def _save_seeds(seeds: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    seen = set()
    out = []
    for s in seeds:
        s = str(s).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    with open(SEEDS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


# ── 索引访问 (mtime 自动重载) ────────────────────────
class IndexStore:
    """索引访问: local 按 mtime 自动重载, meili 惰性单例."""

    def __init__(self):
        self.backend = None
        self.mtime = None

    @staticmethod
    def _mtime() -> float:
        return os.path.getmtime(INDEX_FILE) if os.path.isfile(INDEX_FILE) else 0.0

    def ensure_fresh(self):
        if config.SEARCH_BACKEND == "meili":
            if self.backend is None:
                self.backend = create_backend()
            return
        mt = self._mtime()
        if self.backend is None or mt != self.mtime:
            self.backend = create_backend()
            self.mtime = mt

    def stats(self) -> dict:
        self.ensure_fresh()
        return self.backend.stats()

    def search(self, q, page=1, size=10):
        self.ensure_fresh()
        return self.backend.search(q, page=page, page_size=size)

    def docs(self, page, size):
        """按 doc_id 倒序浏览文档 (统一 dict 列表)."""
        self.ensure_fresh()
        return self.backend.list_docs(page, size)

    def get_doc(self, doc_id):
        self.ensure_fresh()
        return self.backend.get_doc(int(doc_id))

    def remove_doc(self, doc_id) -> bool:
        self.ensure_fresh()
        return self.backend.remove_doc(int(doc_id))

    def rebuild_authority(self) -> dict:
        self.ensure_fresh()
        return self.backend.rebuild_authority()


index_store = IndexStore()


# ── 页面路由 ──────────────────────────────────────────
@app.route("/")
def index_redirect():
    if session.get("admin"):
        return redirect(url_for("admin_home"))
    return redirect(url_for("login"))


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = (request.form.get("password") or "").strip()
        if secrets.compare_digest(pwd, _get_password()):
            session.clear()
            session["admin"] = True
            session["csrf"] = secrets.token_hex(16)
            nxt = request.args.get("next") or url_for("admin_home")
            return redirect(nxt)
        return render_template("admin.html", login_error="密码错误", login_only=True, need_login=True)
    if session.get("admin"):
        return redirect(url_for("admin_home"))
    return render_template("admin.html", login_only=True, need_login=True)


@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/")
@login_required
def admin_home():
    return render_template("admin.html")


# ── API: 认证 / 状态 ─────────────────────────────────
@app.route("/admin/api/session")
def api_session():
    return jsonify({
        "auth_enabled": bool(config.ADMIN.get("auth_enabled", True)),
        "logged_in": bool(session.get("admin")),
        "csrf": session.get("csrf", ""),
    })


@app.route("/admin/api/status")
@login_required
def api_status():
    st = manager.status()
    st["seeds_count"] = len(_load_seeds())
    st["skip_ext"] = sorted(config.SKIP_EXT)
    # 爬虫结束后 bot 引用已清空, 回退显示最近一次任务结果与磁盘索引
    if st["status"] in ("stopped", "error") and st["crawled"] == 0:
        if getattr(manager, "_last_crawled", 0):
            st["crawled"] = manager._last_crawled
            st["errors"] = getattr(manager, "_last_errors", 0)
    if not st.get("index") or not st["index"]:
        try:
            st["index"] = index_store.stats()
        except Exception:  # noqa: BLE001
            st["index"] = {}
    if not st["recent"]:
        # 最近抓取: 从索引取最新文档兜底 (最多 8 条)
        try:
            docs, _ = index_store.docs(1, 8)
            st["recent"] = [{"url": d["url"], "title": d["title"] or d["url"],
                             "tokens": d["word_count"], "links": 0, "time": 0} for d in docs]
        except Exception:  # noqa: BLE001
            pass
    return jsonify(st)


@app.route("/admin/api/config")
@login_required
def api_config_get():
    return jsonify({"ok": True, "config": admin_config.current_values()})


@app.route("/admin/api/config", methods=["POST"])
@login_required
@csrf_required
def api_config_post():
    payload = request.get_json(silent=True) or {}
    cfg = payload.get("config") or payload
    result = admin_config.update(cfg)
    if not result["ok"]:
        return jsonify({"ok": False, "errors": result["errors"]}), 400
    return jsonify({"ok": True, "config": admin_config.current_values()})


# ── API: 种子 ────────────────────────────────────────
@app.route("/admin/api/seeds")
@login_required
def api_seeds_get():
    return jsonify({"ok": True, "seeds": _load_seeds()})


@app.route("/admin/api/seeds", methods=["POST"])
@login_required
@csrf_required
def api_seeds_add():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("urls") or payload.get("url") or payload.get("seeds") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("\n", ",").replace("，", ",").split(",") if x.strip()]
    seeds = _load_seeds()
    added = []
    for u in raw:
        u = str(u).strip()
        if u and u.startswith(("http://", "https://")) and u not in seeds:
            seeds.append(u)
            added.append(u)
    _save_seeds(seeds)
    return jsonify({"ok": True, "added": added, "seeds": seeds})


@app.route("/admin/api/seeds", methods=["DELETE"])
@login_required
@csrf_required
def api_seeds_delete():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("urls") or []
    if isinstance(raw, str):
        raw = [raw]
    seeds = _load_seeds()
    removed = [u for u in raw if u in seeds]
    seeds = [u for u in seeds if u not in removed]
    _save_seeds(seeds)
    return jsonify({"ok": True, "removed": removed, "seeds": seeds})


# ── API: 爬虫控制 ────────────────────────────────────
@app.route("/admin/api/crawler/start", methods=["POST"])
@login_required
@csrf_required
def api_crawler_start():
    payload = request.get_json(silent=True) or {}
    seeds = payload.get("seeds")
    if seeds is None:
        seeds = _load_seeds()
    ok, msg = manager.start(
        seeds=seeds,
        max_pages=payload.get("max_pages"),
        max_depth=payload.get("max_depth"),
        concurrency=payload.get("concurrency"),
        resume=payload.get("resume", True),
    )
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


@app.route("/admin/api/crawler/stop", methods=["POST"])
@login_required
@csrf_required
def api_crawler_stop():
    ok, msg = manager.stop()
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


# ── API: 队列监控 ────────────────────────────────────
@app.route("/admin/api/queue")
@login_required
def api_queue():
    st = manager.status()
    bot = manager._bot
    queue_data = []
    domain_counts = {}
    if bot is not None:
        try:
            frontier = bot.frontier
            # 浅拷贝队列快照 (不弹出)
            entries = list(frontier._queue)
            for e in entries[:200]:
                queue_data.append({
                    "url": e.url,
                    "depth": e.depth,
                    "priority": round(float(e.priority), 1),
                    "anchor": (e.anchor_text or "")[:40],
                })
            for e in entries:
                dom = frontier.get_domain(e.url)
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
            seen_size = len(frontier._seen)
        except Exception as exc:  # noqa: BLE001
            seen_size = 0
            exc = str(exc)
    else:
        seen_size = 0
    domain_counts = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:30]
    return jsonify({
        "ok": True,
        "status": st["status"],
        "pending": st["queue"],
        "total_added": st["total_added"],
        "total_crawled": st["total_crawled"],
        "seen_size": seen_size,
        "queue_sample": queue_data,
        "domain_counts": domain_counts,
    })


# ── API: 索引管理 ────────────────────────────────────
@app.route("/admin/api/index")
@login_required
def api_index_stats():
    return jsonify({"ok": True, "stats": index_store.stats()})


@app.route("/admin/api/docs")
@login_required
def api_docs():
    page = max(1, _safe_int(request.args.get("page"), 1))
    size = min(50, max(1, _safe_int(request.args.get("size"), 20)))
    q = (request.args.get("q") or "").strip()
    if q:
        results, total = index_store.search(q, page=page, size=size)
        docs = [
            {"doc_id": r.doc_id, "url": r.url, "title": r.title,
             "description": r.description, "word_count": r.word_count,
             "score": round(r.score, 4), "authority": round(getattr(r, "authority", 0.0), 6)}
            for r in results
        ]
    else:
        docs, total = index_store.docs(page, size)
    return jsonify({"ok": True, "docs": docs, "total": total, "page": page, "size": size})


@app.route("/admin/api/docs/<int:doc_id>")
@login_required
def api_doc_detail(doc_id):
    d = index_store.get_doc(doc_id)
    if not d:
        return jsonify({"ok": False, "error": "文档不存在"}), 404
    return jsonify({"ok": True, "doc": {
        "doc_id": d["doc_id"], "url": d["url"], "title": d["title"],
        "description": d["description"], "body_text": (d.get("body_text") or "")[:5000],
        "word_count": d["word_count"], "content_length": d.get("content_length", 0),
        "authority": round(float(d.get("authority", 0.0)), 6),
        "simhash": d.get("simhash", 0),
        "outlinks": list(d.get("outlinks", []) or []),
        "fetch_time": d.get("fetch_time", 0) or 0,
    }})


@app.route("/admin/api/docs/<int:doc_id>", methods=["DELETE"])
@login_required
@csrf_required
def api_doc_delete(doc_id):
    if manager.status()["status"] in ("running", "starting"):
        return jsonify({"ok": False, "error": "爬虫运行中禁止删除文档, 请先停止爬虫"}), 400
    ok = index_store.remove_doc(doc_id)
    if not ok:
        return jsonify({"ok": False, "error": "文档不存在"}), 404
    return jsonify({"ok": True})


@app.route("/admin/api/index/rebuild-authority", methods=["POST"])
@login_required
@csrf_required
def api_index_rebuild_authority():
    """基于已索引文档的链接关系重建 PageRank 权威分并持久化."""
    if manager.status()["status"] in ("running", "starting"):
        return jsonify({"ok": False, "error": "爬虫运行中禁止重建权威分, 请先停止爬虫"}), 400
    index_store.ensure_fresh()
    try:
        stats = index_store.rebuild_authority()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": "重建失败: %s" % exc}), 500
    return jsonify({"ok": True, "stats": stats})


# ── API: 任务历史 / 日志 ─────────────────────────────
@app.route("/admin/api/history")
@login_required
def api_history():
    return jsonify({"ok": True, "tasks": manager.list_tasks()})


@app.route("/admin/api/logs")
@login_required
def api_logs():
    from admin_runner import LOG_FILE
    lines = min(500, max(10, _safe_int(request.args.get("lines"), 200)))
    level = (request.args.get("level") or "").upper()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.read().splitlines()
    except OSError:
        return jsonify({"ok": True, "logs": [], "note": "日志文件尚未生成"})
    if level:
        all_lines = [ln for ln in all_lines if ("[%s]" % level) in ln or ("%s:" % level) in ln]
    return jsonify({"ok": True, "logs": all_lines[-lines:]})


# ── 工具函数 ─────────────────────────────────────────
def _safe_int(raw, default):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def main():
    parser = argparse.ArgumentParser(description="xiniubot 管理后台")
    parser.add_argument("--host", default=config.ADMIN.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=config.ADMIN.get("port", 8081))
    args = parser.parse_args()

    print("=" * 52)
    print("  xiniubot 管理后台")
    print("=" * 52)
    print("  访问: http://%s:%d/admin/" % (args.host, args.port))
    print("  认证: %s" % ("开启" if config.ADMIN.get("auth_enabled", True) else "关闭"))
    # 未配置固定密码时, 启动即生成并展示初始密码
    if config.ADMIN.get("auth_enabled", True) and not config.ADMIN.get("password"):
        _get_password()
    print("=" * 52)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
