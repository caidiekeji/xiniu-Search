# -*- coding: utf-8 -*-
"""
xiniubot 管理后台 - 爬虫运行管理
================================
在后台线程中运行 XiniuBot 爬虫, 提供:
  - 启动 / 停止 (优雅停止, 自动保存索引)
  - 实时状态查询 (进度 / 错误 / 队列 / 最近抓取 / 索引统计)
  - 任务历史记录 (写入 data/admin/tasks.json)
  - 独立文件日志 (data/admin/crawler.log, 滚动轮转)

线程模型: 爬虫在独立线程内运行自己的 asyncio 事件循环,
管理端通过读取 bot 上的计数器(整数)与队列长度获取状态 (GIL 保证读取安全).
"""

import asyncio
import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

import config
from main import XiniuBot

LOG_FILE = os.path.join(config.BASE_DIR, "data", "admin", "crawler.log")
TASKS_FILE = os.path.join(config.BASE_DIR, "data", "admin", "tasks.json")
MAX_TASKS = 100

_crawler_logger_ready = False


def _setup_crawler_logger():
    """为 xiniubot 日志器追加滚动文件输出 (幂等)."""
    global _crawler_logger_ready
    if _crawler_logger_ready:
        return
    logger = logging.getLogger("xiniubot")
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            _crawler_logger_ready = True
            return
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(fh)
    _crawler_logger_ready = True


class CrawlerManager:
    """单例式爬虫运行管理器."""

    def __init__(self):
        self._bot = None
        self._thread = None
        self.state = "stopped"       # stopped | starting | running | stopping | error
        self.started_at = None
        self.finished_at = None
        self.params = {}
        self.last_error = ""
        self._last_crawled = 0
        self._last_errors = 0
        self._lock = threading.Lock()

    # ── 状态查询 ──────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            st = {
                "status": self.state,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "running_seconds": 0,
                "params": dict(self.params),
                "last_error": self.last_error,
                "crawled": 0,
                "errors": 0,
                "queue": 0,
                "total_added": 0,
                "total_crawled": 0,
                "index": {},
                "recent": [],
            }
            bot = self._bot
            if self.started_at:
                st["running_seconds"] = round(time.time() - self.started_at, 1)
            if bot is not None:
                st["crawled"] = getattr(bot, "_crawled_count", 0)
                st["errors"] = getattr(bot, "_error_count", 0)
                try:
                    st["queue"] = bot.frontier.pending_count
                    st["total_added"] = bot.frontier.total_added
                    st["total_crawled"] = bot.frontier.total_crawled
                except Exception:
                    pass
                try:
                    st["index"] = bot.engine.index.stats()
                except Exception:
                    st["index"] = {}
                recent = getattr(bot, "_recent", None)
                if recent:
                    st["recent"] = list(recent)
            return st

    # ── 启动 / 停止 ───────────────────────────────────
    def start(self, seeds, max_pages=None, max_depth=None, concurrency=None, resume=True) -> tuple:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, "爬虫正在运行中, 请先停止"
            seeds = [s for s in (seeds or []) if s and str(s).strip()]
            if not seeds:
                return False, "请至少提供一个种子 URL"
            mp = int(max_pages) if max_pages else config.CRAWLER["max_pages"]
            md = int(max_depth) if max_depth else config.CRAWLER["max_depth"]
            cc = int(concurrency) if concurrency else config.CRAWLER["max_concurrent"]
            config.CRAWLER["max_concurrent"] = max(1, cc)

            self.state = "starting"
            self.started_at = time.time()
            self.finished_at = None
            self.last_error = ""
            self.params = {
                "seeds": list(seeds),
                "max_pages": mp,
                "max_depth": md,
                "concurrency": cc,
                "resume": bool(resume),
            }
            self._bot = None
            self._thread = threading.Thread(
                target=self._run, args=(list(seeds), mp, md, bool(resume)), daemon=True, name="xiniubot-crawler"
            )
            self._thread.start()
            return True, "爬虫已启动"

    def stop(self) -> tuple:
        with self._lock:
            bot = self._bot
            if bot is None or self.state not in ("running", "starting"):
                return False, "爬虫未在运行"
            bot._running = False
            if self.state != "stopping":
                self.state = "stopping"
            return True, "停止信号已发送, 正在保存索引并退出..."

    # ── 内部运行 ──────────────────────────────────────
    def _run(self, seeds, max_pages, max_depth, resume=True):
        _setup_crawler_logger()
        logger = logging.getLogger("xiniubot")
        bot = None
        try:
            bot = XiniuBot(seed_urls=seeds, max_pages=max_pages, max_depth=max_depth, resume=resume)
            with self._lock:
                self._bot = bot
                self.state = "running"
            asyncio.run(bot.start())
            self._rebuild_authority(bot)
            with self._lock:
                if self.state != "error":
                    self.state = "stopped"
        except Exception as e:  # noqa: BLE001
            logger.error("爬虫运行异常: %s", e)
            with self._lock:
                self.state = "error"
                self.last_error = str(e)
        finally:
            last_crawled = getattr(bot, "_crawled_count", 0) if bot else 0
            last_errors = getattr(bot, "_error_count", 0) if bot else 0
            with self._lock:
                self.finished_at = time.time()
                self._last_crawled = last_crawled
                self._last_errors = last_errors
                self._bot = None
            self._persist_task()
            logger.info("爬虫任务已结束")

    # ── 任务历史 ──────────────────────────────────────
    def _rebuild_authority(self, bot):
        """按当前后端重建权威分并持久化索引."""
        try:
            stats = bot.engine.rebuild_authority()
            bot.engine.save()
            logger = logging.getLogger("xiniubot")
            logger.info("链接权威分已重建: %s 文档, 后端=%s",
                        stats.get("docs", 0), stats.get("backend", "local"))
        except Exception as e:  # noqa: BLE001
            logger = logging.getLogger("xiniubot")
            logger.error("链接权威分重建失败: %s", e)

    def _persist_task(self):
        try:
            os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
            tasks = []
            if os.path.isfile(TASKS_FILE):
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
            if not isinstance(tasks, list):
                tasks = []
            rec = {
                "id": int(time.time()),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration": round((self.finished_at - self.started_at), 1)
                if (self.finished_at and self.started_at) else 0,
                "status": self.state,
                "last_error": self.last_error,
                "params": dict(self.params),
                "crawled": self._last_crawled,
                "errors": self._last_errors,
            }
            tasks.insert(0, rec)
            tasks = tasks[:MAX_TASKS]
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def list_tasks() -> list:
        if not os.path.isfile(TASKS_FILE):
            return []
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                tasks = json.load(f)
            return tasks if isinstance(tasks, list) else []
        except Exception:  # noqa: BLE001
            return []


# 模块级单例
manager = CrawlerManager()
