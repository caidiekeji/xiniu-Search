"""
xiniubot URL Frontier
======================
基于优先级的 URL 调度队列, 支持:
  - 优先级排序 (BFS 优先, 深度越浅优先级越高)
  - 域名去重与限速 (politeness)
  - URL 去重 (基于规范化后的 URL)
  - 持久化
"""

import asyncio
import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin, urldefrag, urlunparse, parse_qsl, urlencode
from heapq import heappush, heappop

import config


@dataclass(order=True)
class URLEntry:
    priority: float                            # 越小越优先
    url: str = field(compare=False)
    depth: int = field(compare=False, default=0)
    anchor_text: str = field(compare=False, default="")
    referer: str = field(compare=False, default="")


class URLFrontier:
    """
    线程安全的 URL 前端队列.
    """

    def __init__(self, seed_urls: list[str] | None = None):
        self._queue: list[URLEntry] = []           # 最小堆
        self._seen: set[str] = set()               # 已见 URL (规范化后的 fingerprint)
        self._domain_last_access: dict[str, float] = defaultdict(float)
        self._domain_queue: dict[str, list[URLEntry]] = defaultdict(list)
        self._pending_domains: set[str] = set()

        self._lock = asyncio.Lock()
        self._total_added = 0
        self._total_crawled = 0

        # 添加种子 URL
        if seed_urls:
            for url in seed_urls:
                self.add_sync(url, depth=0)

    # ── URL 规范化 ────────────────────────────────────

    @staticmethod
    def normalize_url(url: str) -> str:
        """规范化 URL, 去除 fragment, 统一格式."""
        url = url.strip()
        if not url:
            return ""

        # 去掉 fragment
        url, _ = urldefrag(url)

        # 解析
        parsed = urlparse(url)
        if parsed.scheme not in config.ALLOWED_SCHEMES:
            return ""

        # 统一小写 scheme 和 host
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # 去掉默认端口
        if ":80" in netloc and scheme == "http":
            netloc = netloc.replace(":80", "")
        elif ":443" in netloc and scheme == "https":
            netloc = netloc.replace(":443", "")

        # 去掉末尾斜杠 (仅 path 为 / 时)
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # 去除追踪/统计参数 (utm_*, fbclid, gclid, ref, spm 等)
        query = _clean_query(parsed.query)

        # 标准化
        normalized = urlunparse((scheme, netloc, path, parsed.params, query, ""))
        return normalized

    @staticmethod
    def url_fingerprint(url: str) -> str:
        """URL 指纹 (MD5 哈希) 用于去重."""
        return hashlib.md5(url.encode("utf-8")).hexdigest()

    @staticmethod
    def get_domain(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.lower()

    @staticmethod
    def _should_skip(url: str) -> bool:
        """检查 URL 是否应该跳过."""
        parsed = urlparse(url)

        # scheme 检查
        if parsed.scheme not in config.ALLOWED_SCHEMES:
            return True

        # 扩展名检查
        path_lower = parsed.path.lower()
        for ext in config.SKIP_EXT:
            if path_lower.endswith(ext):
                return True

        # URL 长度检查
        if len(url) > config.CRAWLER["max_url_length"]:
            return True

        return False

    # ── 添加 URL ──────────────────────────────────────

    def add_sync(self, url: str, depth: int = 0, anchor_text: str = "", referer: str = "") -> bool:
        """同步添加 (用于初始化)."""
        normalized = self.normalize_url(url)
        if not normalized or self._should_skip(normalized):
            return False

        fp = self.url_fingerprint(normalized)
        if fp in self._seen:
            return False

        self._seen.add(fp)
        domain = self.get_domain(normalized)
        entry = URLEntry(
            priority=depth,
            url=normalized,
            depth=depth,
            anchor_text=anchor_text,
            referer=referer,
        )
        heappush(self._queue, entry)
        self._total_added += 1
        return True

    async def add(self, url: str, depth: int = 0, anchor_text: str = "", referer: str = "") -> bool:
        """异步添加 URL."""
        async with self._lock:
            return self.add_sync(url, depth, anchor_text, referer)

    async def add_batch(self, urls: list[tuple[str, str]], depth: int, referer: str = ""):
        """批量添加: urls = [(url, anchor_text), ...]"""
        async with self._lock:
            for url, anchor in urls:
                self.add_sync(url, depth, anchor, referer)

    # ── 弹出 URL ──────────────────────────────────────

    async def get_next(self) -> URLEntry | None:
        """获取下一个待爬取的 URL (遵守礼貌延迟)."""
        async with self._lock:
            now = time.time()
            delay = config.CRAWLER["politeness_delay"]

            # 遍历队列找到第一个符合礼貌延迟的候选项
            candidates = []
            while self._queue:
                entry = heappop(self._queue)
                domain = self.get_domain(entry.url)
                last_access = self._domain_last_access.get(domain, 0)

                if now - last_access >= delay:
                    # 可以立即访问
                    self._domain_last_access[domain] = now
                    for _, c in candidates:
                        heappush(self._queue, c)
                    self._total_crawled += 1
                    return entry
                else:
                    candidates.append((last_access + delay - now, entry))

            # 全部处于礼貌期: 精确等待最短时长后重试, 避免固定自旋
            min_wait = min((w for w, _ in candidates), default=0.0) if candidates else 0.0
            for _, c in candidates:
                heappush(self._queue, c)

        if candidates and min_wait > 0:
            await asyncio.sleep(min_wait)
            return await self.get_next()

        return None

    # ── 状态 ──────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def total_added(self) -> int:
        return self._total_added

    @property
    def total_crawled(self) -> int:
        return self._total_crawled

    def has_urls(self) -> bool:
        return len(self._queue) > 0

    # ── 持久化 ────────────────────────────────────────

    def save(self, path: str | None = None):
        path = path or os.path.join(config.BASE_DIR, "data", "frontier.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "seen": list(self._seen),
            "queue": [
                {"url": e.url, "depth": e.depth, "anchor": e.anchor_text, "priority": e.priority}
                for e in self._queue
            ],
            "total_added": self._total_added,
            "total_crawled": self._total_crawled,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str | None = None):
        path = path or os.path.join(config.BASE_DIR, "data", "frontier.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._seen = set(data.get("seen", []))
            self._total_added = data.get("total_added", 0)
            self._total_crawled = data.get("total_crawled", 0)
            self._queue = []
            for item in data.get("queue", []):
                entry = URLEntry(
                    priority=item.get("priority", 0),
                    url=item["url"],
                    depth=item.get("depth", 0),
                    anchor_text=item.get("anchor", ""),
                )
                heappush(self._queue, entry)
        except Exception:
            pass


def _clean_query(query: str) -> str:
    """清洗 query 中的追踪参数, 保留其余参数原序."""
    if not query:
        return ""
    try:
        pairs = [
            (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k.lower() not in config.URL_TRACKING_PARAMS
        ]
    except Exception:
        return query
    if not pairs:
        return ""
    return urlencode(pairs)
