# 后台作业的读写门面 + 重启收尾（评审遗留：进程被杀时"跑完没有"必须答得出来）。
#
# 设计取舍：作业行只记"谁/何时/什么范围/发起什么"与**终局状态**；**进度就地取文档状态机**
# （前端本来就在轮询它），所以这里没有"再来一套进度"的野心——两套进度必然漂移。
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import BackgroundJob, Document

ROOT_KINDS = ("reindex",)


def create_job(session: Session, *, kind: str, scope: dict, total: int, created_by: str,
               status: str = "running") -> BackgroundJob:
    assert kind in ROOT_KINDS, f"未登记的作业类型：{kind}"
    job = BackgroundJob(tenant_id="default", kind=kind, status=status, scope=scope,
                        total=total, created_by=created_by)
    session.add(job)
    session.flush()
    return job


def finish_job(session: Session, job: BackgroundJob | None, *, done: int, failed: int,
               error: str | None = None) -> None:
    """给作业收尾。done/failed 由执行器按篇累计；error 只用于整轮性故障。"""
    if job is None:
        return
    job.done, job.failed = done, failed
    job.status = "failed" if error else "done"
    job.error = error
    job.finished_at = datetime.now(timezone.utc)


def finish_progress(session: Session, job_id: int, *, done: int, failed: int) -> None:
    """只更新进度计数（**不动 status**）：终局状态由 finish_job/重启收尾决定，
    避免"跑了 3 篇就以为结束了"这类中间态被误读成终局。"""
    job = session.get(BackgroundJob, job_id)
    if job is None:
        return
    job.done, job.failed = done, failed
    session.commit()


def report_doc_outcome(session: Session, job_id: int, *, ok: bool) -> None:
    """**异步档**（ARQ worker）每处理完一篇就报一次：计数 +1，全部结清即收尾。

    为什么必须由 worker 报：异步档下每篇的结果只在 worker 手里，backend 只负责建作业行 + 入队。
    **此前漏了这一步**——作业行建完就永远停在 `queued`、done/failed 恒为 0，界面上"排队中"永不结束
    （文档其实已经 ready，只有作业行在撒谎）。做交付验证时它会以"异步档点一次重建，
    文档全好但作业面板卡住"的形态露出来。

    为什么取行锁：ARQ worker 的 `max_jobs > 1`，同一作业的多篇会被并发处理，读-改-写会丢计数
    （表现同样是"永远收不了尾"）。行锁把同一作业的计数串行化，不同作业互不阻塞。
    终局语义复用 `finish_job`——"done 还是 failed"只允许有一个定义处。
    """
    from sqlalchemy import select

    job = session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id)
                          .with_for_update()).scalar_one_or_none()
    if job is None:
        return          # 作业行被删了（或本来就没登记）：worker 不该因此失败
    if ok:
        job.done += 1
    else:
        job.failed += 1
    if job.finished_at is None and job.done + job.failed >= job.total:
        finish_job(session, job, done=job.done, failed=job.failed)
    session.commit()


def recover_interrupted_jobs(engine, *, has_external_worker: bool) -> dict:
    """进程重启后的收尾：把上一次留下的烂摊子说明白。返回 {jobs, documents} 处理计数。

    - **作业**：running 的必然已随进程消失 → 标 `interrupted`；`queued` 的归外部 worker，
      不动（ARQ worker 与 backend 是两个进程，backend 重启不影响它）。
    - **文档**：仅在**没有外部 worker**（同步档）时，把范围内卡在 pending/parsing 的文档标
      `failed`——它们**没有任何东西会去处理**，会永远显示"排队中"，这正是评审发现的失败形态。
      异步档绝不能动：那些 pending 正是排队等 worker 的任务，误标会把正常入库变成失败。
    """
    jobs_n = docs_n = 0
    with Session(engine) as s:
        stuck = s.query(BackgroundJob).filter_by(status="running").all()
        for job in stuck:
            job.status = "interrupted"
            job.error = "服务重启中断（进程内的后台任务随进程结束）"
            job.finished_at = datetime.now(timezone.utc)
            jobs_n += 1
            if has_external_worker:
                continue
            kb_ids = (job.scope or {}).get("kb_ids")
            q = s.query(Document).filter(Document.status.in_(("pending", "parsing")))
            if kb_ids is not None:
                q = q.filter(Document.kb_id.in_(kb_ids))
            for doc in q.all():
                doc.status = "failed"
                doc.error = "入库被服务重启中断：点「重建本库索引」或单篇「重试」即可继续"
                docs_n += 1
        s.commit()
    return {"jobs": jobs_n, "documents": docs_n}
