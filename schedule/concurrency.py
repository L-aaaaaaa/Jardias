"""
schedule/concurrency.py — 并发控制器。
"""
import asyncio


class ConcurrencyController:
    """基于 asyncio.Semaphore 控制最大并发任务数。"""

    def __init__(self, max_concurrent: int = 3):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._sem = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self):
        await self._sem.acquire()

    async def __aexit__(self, *args):
        self._sem.release()
