# 响应形状：DB 行 → 契约里那份 JSON。全部是纯函数（除了取明文 key 需要的 secret 之外无副作用）。
#
# 为什么单独一个模块：这些函数的输出**必须与 app/api/schemas.py 里的响应模型逐字对齐**——
# pydantic 会把模型没声明的键默默丢掉（评测 run 的 judge 曾这样消失过一次），
# 所以"响应形状"是一个该被集中看、集中改的东西：字段名改了要动的就这两个文件。
from app.models import ApiKey, BackgroundJob, Document, EvalItemResult, EvalQuestion, EvalRun, ModelConfig, User
from app.services.crypto import decrypt_secret


def doc_json(d: Document) -> dict:
    return {"id": d.id, "kb_id": d.kb_id, "name": d.name, "status": d.status,
            "error": d.error, "size_bytes": d.size_bytes,
            "created_at": d.created_at}


def user_json(u: User, grants: dict[int, list[int]]) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
            "status": u.status, "created_at": u.created_at.isoformat(),
            "kb_ids": None if u.role == "admin" else grants.get(u.id, []),
            "daily_token_limit": u.daily_token_limit,
            "monthly_token_limit": u.monthly_token_limit}


def model_json(m: ModelConfig, secret: str) -> dict:
    plain = decrypt_secret(m.encrypted_api_key, secret)
    return {"id": m.id, "scenario": m.scenario, "provider": m.provider,
            "base_url": m.base_url, "model_name": m.model_name,
            "capabilities": m.capabilities, "is_default": m.is_default,
            "fallback_rank": m.fallback_rank, "enabled": m.enabled,
            "api_key_masked": ("****" + plain[-4:]) if plain else ""}


def api_key_json(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "key_prefix": k.key_prefix, "kb_ids": k.kb_ids,
            "monthly_token_quota": k.monthly_token_quota, "enabled": k.enabled,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat()}


def branding_json(brand_name, logo) -> dict:
    return {"brand_name": brand_name, "logo": logo}


def question_json(q: EvalQuestion) -> dict:
    return {"id": q.id, "question": q.question, "expect_all": q.expect_all or [],
            "expect_any": q.expect_any or [], "cites": q.cites or [],
            "category": q.category or "", "note": q.note, "enabled": bool(q.enabled),
            "created_at": q.created_at}


def run_json(r: EvalRun) -> dict:
    return {"id": r.id, "status": r.status, "total": r.total, "passed": r.passed,
            "metrics": r.metrics or {}, "kb_ids": r.kb_ids, "judge": bool(r.judge),
            "chat_model": r.chat_model, "embedding_model": r.embedding_model,
            "error": r.error, "created_by": r.created_by,
            "started_at": r.started_at, "finished_at": r.finished_at}


def item_json(i: EvalItemResult) -> dict:
    return {"id": i.id, "question_id": i.question_id, "question": i.question,
            "category": i.category or "", "note": i.note,
            "expect_all": i.expect_all or [], "expect_any": i.expect_any or [],
            "cites": i.cites or [], "answer": i.answer,
            "cited_docs": i.cited_docs or [], "top_docs": i.top_docs or [],
            "checks": i.checks or {}, "judge": i.judge, "passed": bool(i.passed),
            "rank": i.rank, "latency_ms": i.latency_ms, "error": i.error}


def job_json(j: BackgroundJob) -> dict:
    return {"id": j.id, "kind": j.kind, "status": j.status, "scope": j.scope or {},
            "total": j.total, "done": j.done, "failed": j.failed, "error": j.error,
            "created_by": j.created_by, "started_at": j.started_at,
            "finished_at": j.finished_at}
