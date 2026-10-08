# 任务队列抽象：上传走 enqueue（ARQ），测试注入 Recording fake，默认无队列=同步执行
from typing import Protocol

# 任务名的唯一来源是 worker 侧那个函数（理由见 app/worker.IMPORT_JOB_NAME）：
# 入队侧**不许再写字符串字面量**——两处字面量一旦漂移，arq 找不到同名函数就丢任务，
# 表现是"全绿但文档永远 pending"，没有任何报错会冒到用户面前（交付验证时必踩）。
from app.worker import IMPORT_JOB_NAME


class ImportQueue(Protocol):
    def enqueue_import(self, document_id: int, job_id: int | None = None) -> None: ...


class ArqQueue:
    """ARQ 适配器：入队 import_document 任务。Redis 连接参数走配置。"""

    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379):
        self._host = redis_host
        self._port = redis_port

    async def _enqueue(self, document_id: int, job_id: int | None = None) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings(host=self._host, port=self._port))
        # 作业号随任务一起交给 worker：异步档的进度与收尾只有执行方知道（它才看得到每篇的结果），
        # backend 建完作业行就不该再假装自己知道进度
        await pool.enqueue_job(IMPORT_JOB_NAME, document_id, job_id)
        await pool.aclose()

    def enqueue_import(self, document_id: int, job_id: int | None = None) -> None:
        import asyncio

        asyncio.run(self._enqueue(document_id, job_id))
