"""随机间隔限频器"""
import random
import time

from loguru import logger


class RateLimiter:
    """在 [min_seconds, max_seconds] 范围内随机 sleep"""

    def __init__(self, min_seconds: float, max_seconds: float):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds

    def wait(self) -> float:
        """随机等待,返回实际等待秒数"""
        delay = random.uniform(self.min_seconds, self.max_seconds)
        logger.debug(f"限频等待 {delay:.1f}s")
        time.sleep(delay)
        return delay

    @classmethod
    def search_limiter(cls) -> "RateLimiter":
        """公众号搜索限频:20-60s"""
        return cls(min_seconds=20, max_seconds=60)

    @classmethod
    def article_limiter(cls) -> "RateLimiter":
        """文章抓取限频:5-20s"""
        return cls(min_seconds=5, max_seconds=20)
