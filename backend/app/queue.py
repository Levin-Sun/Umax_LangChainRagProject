# 任务队列抽象：上传走 enqueue（ARQ），测试注入 Recording fake，默认无队列=同步执行
from typing import Protocol


class ImportQueue(Protocol):
    def enqueue_import(self, document_id: int) -> None: ...


class ArqQueue:
    """ARQ 适配器：入队 import_document 任务。Redis 连接参数走配置。"""

    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379):
        self._host = redis_host
        self._port = redis_port

    async def _enqueue(self, document_id: int) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings(host=self._host, port=self._port))
        await pool.enqueue_job("import_document", document_id)
        await pool.aclose()

    def enqueue_import(self, document_id: int) -> None:
        import asyncio

        asyncio.run(self._enqueue(document_id))
