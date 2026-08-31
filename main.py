#!/usr/bin/env python3
"""
xiniubot 全网爬虫
=================
使用方法:
  python main.py --seeds "https://example.com,https://news.ycombinator.com"
  python main.py --seeds-file seeds.txt
  python main.py --seeds "https://www.zhihu.com" --max-pages 1000 --max-depth 3
"""

import argparse
from collections import deque
import asyncio
import logging
import os
import signal
import sys
import time

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from crawler.frontier import URLFrontier
from crawler.downloader import AsyncDownloader
from crawler.parser import parse_html
from indexer.tokenizer import tokenize
from search.backends import create_backend

# ── 日志配置 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xiniubot")


class XiniuBot:
    """
    xiniubot 全网爬虫引擎.
    """

    def __init__(self, seed_urls: list[str], max_pages: int | None = None, max_depth: int | None = None, resume: bool = True):
        self.frontier = URLFrontier(None)
        self._resumed = False
        if resume:
            # 断点续爬: 加载上次未完成的队列与去重集合, 再并入新种子 (按指纹去重)
            self.frontier.load()
            self._resumed = self.frontier.pending_count > 0
        for url in seed_urls:
            self.frontier.add_sync(url, depth=0)
        self.downloader = AsyncDownloader()
        self.engine = create_backend()
        self.max_pages = max_pages or config.CRAWLER["max_pages"]
        self.max_depth = max_depth or config.CRAWLER["max_depth"]

        self._running = True
        self._start_time = 0.0
        # 续爬时从已保存的累计抓取数继续, 保持 max_pages 预算连续
        self._crawled_count = self.frontier.total_crawled if self._resumed else 0
        self._error_count = 0
        self._save_interval = 100   # 每爬 N 页保存一次
        self._recent = deque(maxlen=50)  # 最近抓取记录 (供后台面板)

    async def start(self):
        """启动爬虫."""
        # 创建数据目录
        for d in config.STORAGE.values():
            os.makedirs(d, exist_ok=True)

        await self.downloader.start()
        self._start_time = time.time()

        logger.info("=" * 60)
        logger.info("  xiniubot 搜索引擎爬虫 启动")
        logger.info("=" * 60)
        logger.info(f"  种子数: {self.frontier.total_added}")
        logger.info(f"  最大页面: {self.max_pages}")
        logger.info(f"  最大深度: {self.max_depth}")
        logger.info(f"  并发数: {config.CRAWLER['max_concurrent']}")
        if self._resumed:
            logger.info(f"  断点续爬: 恢复待爬队列 {self.frontier.pending_count} 条, 已累计抓取 {self.frontier.total_crawled} 页")
        logger.info(f"  索引统计: {self.engine.index.stats()}")
        logger.info("=" * 60)

        try:
            await self._crawl_loop()
        except KeyboardInterrupt:
            logger.info("收到中断信号, 正在保存...")
        finally:
            await self._shutdown()

    def _drain_done(self, done: set[asyncio.Task]):
        """收敛已完成任务的结果/异常, 保证异常不会静默丢失."""
        for task in done:
            if task.cancelled():
                continue
            try:
                task.result()
            except Exception as e:
                logger.error(f"任务异常: {e}")

    async def _crawl_loop(self):
        """主爬取循环."""
        tasks: set[asyncio.Task] = set()
        max_concurrent = config.CRAWLER["max_concurrent"]

        while self._running and self._crawled_count < self.max_pages:
            # 控制并发任务数
            while len(tasks) >= max_concurrent:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                self._drain_done(done)
                tasks = pending

            # 获取下一个 URL
            entry = await self.frontier.get_next()
            if entry is None:
                if not tasks:
                    logger.info("队列为空, 爬虫结束")
                    break
                # 等待进行中的任务释放空位
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                self._drain_done(done)
                tasks = pending
                continue

            if entry.depth > self.max_depth:
                continue

            # 创建爬取任务
            task = asyncio.create_task(self._crawl_url(entry.url, entry.depth, entry.anchor_text))
            tasks.add(task)

        # 等待所有剩余任务
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _crawl_url(self, url: str, depth: int, anchor_text: str = ""):
        """爬取单个 URL."""
        if self._crawled_count >= self.max_pages:
            return

        # 下载
        result = await self.downloader.download(url)
        if result.error:
            logger.debug(f"[FAIL] {url}: {result.error}")
            self._error_count += 1
            return

        if result.status != 200:
            logger.debug(f"[SKIP] {url}: HTTP {result.status}")
            return

        # 解析 (to_thread: 移出事件循环, 避免阻塞其它在途下载的调度)
        try:
            page = await asyncio.to_thread(parse_html, url, result.body, result.encoding)
        except Exception as e:
            logger.debug(f"[PARSE ERROR] {url}: {e}")
            return

        # 合并文本用于分词
        full_text = f"{page.title}\n{page.description}\n{page.body_text}"
        if len(full_text.strip()) < 10:
            return

        # 分词 (to_thread: 与解析一致, 纯函数 offload 线程池)
        tokens = await asyncio.to_thread(tokenize, full_text)
        if len(tokens) < 2:
            return

        # 内容近似去重 (SimHash): 与已索引文档海明距离 <= 阈值则跳过
        dup_doc = self.engine.check_duplicate(tokens)
        if dup_doc is not None:
            logger.info(f"[DEDUP] 内容近似重复, 跳过 {url[:80]} (与文档 #{dup_doc} 相似)")
            return

        # 添加到索引 (记录出链用于链接分析, 标题/描述词用于字段加权)
        doc_id = self.engine.add_page(
            url=url,
            title=page.title,
            description=page.description,
            body_text=page.body_text,
            tokens=tokens,
            outlinks=[u for u, _ in page.links],
        )

        self._crawled_count += 1

        logger.info(
            f"[{self._crawled_count}/{self.max_pages}] "
            f"depth={depth} tokens={len(tokens)} "
            f"links={len(page.links)} "
            f"{url[:80]}"
        )

        # 记录最近抓取 (供后台实时面板)
        self._recent.append({
            "url": url,
            "title": page.title[:60],
            "depth": depth,
            "tokens": len(tokens),
            "links": len(page.links),
            "time": time.time(),
        })

        # 将发现的链接加入队列
        if depth < self.max_depth:
            await self.frontier.add_batch(page.links, depth=depth + 1, referer=url)

        # 定期保存
        if self._crawled_count % self._save_interval == 0:
            self._save_state()

    def _save_state(self):
        """保存爬虫状态."""
        self.engine.save()
        self.frontier.save()
        elapsed = time.time() - self._start_time
        rate = self._crawled_count / elapsed if elapsed > 0 else 0
        logger.info(
            f"[STATS] 爬取: {self._crawled_count} | "
            f"错误: {self._error_count} | "
            f"队列: {self.frontier.pending_count} | "
            f"词典: {self.engine.index.vocabulary_size} | "
            f"速率: {rate:.1f} 页/秒"
        )

    async def _shutdown(self):
        """关闭爬虫, 保存状态."""
        logger.info("正在保存索引和状态...")
        self._save_state()
        await self.downloader.close()
        logger.info("xiniubot 已关闭")


# ── 命令行入口 ────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="xiniubot 全网搜索引擎爬虫")
    parser.add_argument("--seeds", type=str, help="种子URL, 逗号分隔")
    parser.add_argument("--seeds-file", type=str, help="种子URL文件 (每行一个)")
    parser.add_argument("--max-pages", type=int, default=10000, help="最大爬取页面数")
    parser.add_argument("--max-depth", type=int, default=5, help="最大爬取深度")
    parser.add_argument("--concurrency", type=int, default=20, help="最大并发数")
    args = parser.parse_args()

    # 收集种子 URL
    seeds = []
    if args.seeds:
        seeds.extend(u.strip() for u in args.seeds.split(",") if u.strip())
    if args.seeds_file:
        with open(args.seeds_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    seeds.append(line)

    if not seeds:
        print("请提供种子URL: --seeds 'https://example.com' 或 --seeds-file seeds.txt")
        sys.exit(1)

    # 更新配置
    config.CRAWLER["max_concurrent"] = args.concurrency

    # 启动爬虫
    bot = XiniuBot(
        seed_urls=seeds,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
    )

    # 信号处理
    def signal_handler(sig, frame):
        logger.info("收到停止信号...")
        bot._running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
