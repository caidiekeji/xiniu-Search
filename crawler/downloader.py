"""
xiniubot 异步网页下载器
======================
特性:
  - aiohttp 异步 HTTP 客户端
  - 并发控制 (信号量)
  - 自动编码检测 (chardet)
  - 大小限制
  - 重试机制
  - robots.txt 缓存
"""

import asyncio
import logging
import time
from collections import defaultdict
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
import chardet

import config

logger = logging.getLogger("xiniubot.downloader")


class _RetryableError(Exception):
    """内部标记: 服务端 5xx 等可重试错误."""


class RobotsCache:
    """robots.txt 缓存."""

    def __init__(self):
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def can_fetch(self, url: str, user_agent: str) -> bool:
        if not config.CRAWLER["respect_robots"]:
            return True

        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        async with self._lock:
            if domain not in self._cache:
                rp = RobotFileParser()
                robots_url = f"{domain}/robots.txt"
                try:
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(robots_url, headers={"User-Agent": user_agent}) as resp:
                            if resp.status == 200:
                                text = await resp.text(errors="ignore")
                                rp.parse(text.splitlines())
                            else:
                                # 没有 robots.txt, 允许所有
                                rp.allow_all = True
                except Exception:
                    rp.allow_all = True
                self._cache[domain] = rp

            rp = self._cache[domain]
            try:
                return rp.can_fetch(user_agent, url)
            except Exception:
                return True


class PageResult:
    """下载结果."""
    __slots__ = ("url", "status", "content_type", "body", "encoding", "fetch_time", "error")

    def __init__(self):
        self.url: str = ""
        self.status: int = 0
        self.content_type: str = ""
        self.body: bytes = b""
        self.encoding: str = "utf-8"
        self.fetch_time: float = 0.0
        self.error: str = ""


class AsyncDownloader:
    """异步网页下载器."""

    def __init__(self):
        self._semaphore = asyncio.Semaphore(config.CRAWLER["max_concurrent"])
        self._robots = RobotsCache()
        self._session: aiohttp.ClientSession | None = None
        self._domain_last_request: dict[str, float] = defaultdict(float)
        self._stats = {
            "requests": 0,
            "success": 0,
            "errors": 0,
            "robots_blocked": 0,
        }

    async def start(self):
        """启动下载器."""
        timeout = aiohttp.ClientTimeout(total=config.CRAWLER["timeout"])
        connector = aiohttp.TCPConnector(
            limit=config.CRAWLER["max_concurrent"],
            limit_per_host=2,
            ttl_dns_cache=600,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": config.CRAWLER["user_agent"]},
        )

    async def close(self):
        """关闭下载器."""
        if self._session:
            await self._session.close()

    async def download(self, url: str) -> PageResult:
        """
        下载一个 URL. 返回 PageResult.
        """
        result = PageResult()
        result.url = url

        async with self._semaphore:
            # robots.txt 检查
            if not await self._robots.can_fetch(url, config.CRAWLER["user_agent"]):
                result.error = "blocked by robots.txt"
                self._stats["robots_blocked"] += 1
                return result

            # 礼貌延迟
            domain = urlparse(url).netloc
            now = time.time()
            elapsed = now - self._domain_last_request[domain]
            delay = config.CRAWLER["politeness_delay"]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)

            self._domain_last_request[domain] = time.time()
            self._stats["requests"] += 1

            # 重试下载
            for attempt in range(config.CRAWLER["max_retries"]):
                try:
                    start_time = time.time()
                    async with self._session.get(
                        url,
                        allow_redirects=True,
                        max_redirects=5,
                    ) as resp:
                        result.status = resp.status
                        result.content_type = resp.content_type or ""

                        # 5xx 视为可重试的服务端错误, 抛标记后走指数退避重试
                        if resp.status >= 500:
                            result.error = f"HTTP {result.status} (attempt {attempt + 1})"
                            raise _RetryableError()

                        # 校验响应内容是否允许处理
                        ct_base = result.content_type.split(";")[0].strip().lower()
                        if ct_base not in config.ALLOWED_CONTENT_TYPES:
                            result.error = f"unsupported content type: {result.content_type}"
                            return result

                        # 大小限制
                        content_length = resp.content_length or 0
                        if content_length > config.CRAWLER["max_page_size"]:
                            result.error = f"page too large: {content_length}"
                            return result

                        result.body = await resp.read()

                        if len(result.body) > config.CRAWLER["max_page_size"]:
                            result.error = f"page too large: {len(result.body)}"
                            return result

                        result.fetch_time = time.time() - start_time

                        # 非 2xx (如 4xx) 判为客户端错误, 不重试、不计成功
                        if not (200 <= resp.status < 300):
                            result.error = f"HTTP {result.status}"
                            result.body = b""
                            return result

                        # 编码检测
                        content_type_charset = resp.charset
                        if content_type_charset:
                            result.encoding = content_type_charset
                        else:
                            detected = chardet.detect(result.body[:4096])
                            result.encoding = detected.get("encoding") or "utf-8"

                        self._stats["success"] += 1
                        return result

                except _RetryableError:
                    # 5xx 重试标记: 交回底部指数退避逻辑
                    pass
                except asyncio.TimeoutError:
                    result.error = f"timeout (attempt {attempt + 1})"
                except aiohttp.ClientError as e:
                    result.error = f"client error: {e} (attempt {attempt + 1})"
                except Exception as e:
                    result.error = f"unexpected error: {e} (attempt {attempt + 1})"
                    break

                if attempt < config.CRAWLER["max_retries"] - 1:
                    await asyncio.sleep(2 ** attempt)

            self._stats["errors"] += 1
            return result

    @property
    def stats(self) -> dict:
        return dict(self._stats)
