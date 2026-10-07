# 评测体系（§阶段2「评测体系正式化」）：金标准集管理 + 一次评测的全生命周期。
# 评测是"证明更准"的唯一手段，所以它自己的行为也要被钉住：分数怎么算、失败怎么记、
# 题目被删后历史记录还在不在、没有模型 key 时还能不能出检索指标。
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import (AuditLog, EvalItemResult, EvalQuestion, EvalRun, UsageRecord)
from tests.conftest import login, seed_user

ADMIN = ("admin@umax.local", "Adm1n-Pass-123")
MEMBER = ("dev@umax.local", "Dev1-Pass-123")
DOC_NAME = "rag_dirty_doc_01.txt"
DOC_BODY = ("售后处理流程：客户申请退货，先核对订单号。生鲜不支持七天无理由退货，"
            "但坏果要按比例赔偿。客户说破损的优先补发，不要先要求寄回。" * 2)


def _chat(query, hits):
    """假模型：只依据第一条命中作答并标 [1]——与真模型的行为契约一致（带引用生成）。"""
    return {"answer": f"生鲜不支持七天无理由退货，坏果按比例赔偿 [1]。",
            "prompt_tokens": 11, "completion_tokens": 7, "model": "fake-chat"}


def _sync_spawn(fn):
    """测试注入：后台任务同步执行——"跑完再看结果"在单测里必须是确定性的，不靠 sleep 赌时序。
    真线程路径另有一条测试专测（注入 None）。"""
    fn()


def _client(engine, tmp_path, *, chat_fn=_chat, spawn=_sync_spawn, judge_fn=None):
    return TestClient(create_app(engine=engine, secret="k-secret", embedder=None,
                                 chat_fn=chat_fn, upload_dir=str(tmp_path), spawn=spawn,
                                 judge_fn=judge_fn))


@pytest.fixture
def client(engine, db, tmp_path):
    c = _client(engine, tmp_path)
    seed_user(engine, *ADMIN, role="admin")
    seed_user(engine, *MEMBER, role="member")
    login(c, *ADMIN)
    return c


def _mk_kb_with_doc(c, name=DOC_NAME):
    kb = c.post("/api/v1/kb", json={"name": "评测库"}).json()
    c.post(f"/api/v1/kb/{kb['id']}/documents",
           files={"file": (name, DOC_BODY.encode(), "text/plain")})
    return kb


def _mk_question(c, question="生鲜能七天无理由退货吗", *, expect_all=("不支持",),
                 expect_any=(), cites=(DOC_NAME,), category="错别字干扰", note="校准记录"):
    return c.post("/api/v1/eval/questions", json={
        "question": question, "expect_all": list(expect_all), "expect_any": list(expect_any),
        "cites": list(cites), "category": category, "note": note}).json()


# ---- 金标准集 CRUD ----
def test_question_crud_roundtrip_and_audit(client, engine):
    q = _mk_question(client)
    assert q["question"] == "生鲜能七天无理由退货吗" and q["enabled"] is True
    assert q["expect_all"] == ["不支持"] and q["cites"] == [DOC_NAME]
    listed = client.get("/api/v1/eval/questions").json()
    assert [x["id"] for x in listed] == [q["id"]]
    # PATCH 只带一个字段：其余字段不动（缺省=不改，与 settings/用户管理同一套语义）
    r = client.patch(f"/api/v1/eval/questions/{q['id']}", json={"category": "版本冲突"})
    assert r.status_code == 200 and r.json()["category"] == "版本冲突"
    assert r.json()["expect_all"] == ["不支持"]
    # 停用后不再参与评测（真题不合用就停用，不必删——留痕比删干净有用）
    assert client.patch(f"/api/v1/eval/questions/{q['id']}",
                        json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/v1/eval/questions/{q['id']}").status_code == 204
    assert client.delete(f"/api/v1/eval/questions/{q['id']}").status_code == 404
    with Session(engine) as s:
        actions = [a.action for a in s.query(AuditLog).order_by(AuditLog.id)
                   if a.action.startswith("eval_question")]
    # 两次 PATCH 各留一条：改标尺的每一步都要能事后追溯（"上次 18/20 是不是因为改过期望词"）
    assert actions == ["eval_question_created", "eval_question_updated",
                       "eval_question_updated", "eval_question_deleted"]


def test_question_patch_404_and_validation(client):
    assert client.patch("/api/v1/eval/questions/9999", json={"note": "x"}).status_code == 404
    assert client.delete("/api/v1/eval/questions/9999").status_code == 404
    # category 列宽入约（String(64)）：超长是合法 JSON 但存不下，必须 422 拒收
    assert client.post("/api/v1/eval/questions",
                       json={"question": "q", "category": "c" * 65}).status_code == 422


# ---- 一次评测：分数 / 明细 / 记账 ----
def test_run_eval_scores_items_and_records_usage(client, engine):
    kb = _mk_kb_with_doc(client)
    good = _mk_question(client)
    # 第二题问的还是同一篇资料（检索必命中），但期望词资料里没有 → 生成层答不出 → 该题不过
    # （这样才验的是"生成不达标"，而不是"检索没命中"——两类失败必须可分辨）
    bad = _mk_question(client, "生鲜退货的运费由谁承担", expect_all=("运费由商家承担",),
                       expect_any=(), cites=(DOC_NAME,), category="缺失内容")
    r = client.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "done" and (run["total"], run["passed"]) == (2, 1)
    m = run["metrics"]
    assert (m["total"], m["passed"], m["pass_rate"]) == (2, 1, 0.5)
    # 两题的检索都命中同一篇金标准文档（BM25 单路，无向量），故命中率 100%、MRR 1.0
    assert (m["with_cites"], m["hit"], m["mrr"]) == (2, 2, 1.0)
    detail = client.get(f"/api/v1/eval/runs/{run['id']}").json()
    items = {i["question"]: i for i in detail["items"]}
    assert items[good["question"]]["passed"] is True
    assert items[good["question"]]["cited_docs"] == [DOC_NAME]
    assert items[bad["question"]]["passed"] is False
    assert items[bad["question"]]["checks"]["kw_all"] is False
    assert items[bad["question"]]["checks"]["retrieval"] is True   # 检索没问题，是答案没达标
    assert all(i["latency_ms"] is not None for i in detail["items"])
    # 评测是真花钱的调用：每题都要落台账，且记在发起人头上（不是占位邮箱）
    with Session(engine) as s:
        used = s.query(UsageRecord).filter_by(scenario="chat").all()
    assert len(used) == 2 and {u.user_email for u in used} == {ADMIN[0]}
    assert sum(u.prompt_tokens for u in used) == 22
    assert sum(u.completion_tokens for u in used) == 14


def test_run_eval_scope_and_validation(client):
    kb = _mk_kb_with_doc(client)
    assert client.post("/api/v1/eval/runs", json={}).status_code == 400      # 没有启用的题
    _mk_question(client)
    assert client.post("/api/v1/eval/runs",
                       json={"kb_ids": [99999]}).status_code == 400          # 库不存在
    # 限定到空库：检索无命中 → 走未命中兜底，通过 0 但检索指标照算（未命中记 0）
    empty = client.post("/api/v1/kb", json={"name": "空库"}).json()
    run = client.post("/api/v1/eval/runs", json={"kb_ids": [empty["id"]]}).json()
    assert (run["status"], run["passed"]) == ("done", 0)
    assert run["metrics"]["mrr"] == 0.0 and run["metrics"]["hit_rate"] == 0.0
    assert kb["id"] != empty["id"]


def test_run_eval_without_chat_model_still_yields_retrieval_metrics(engine, db, tmp_path):
    """没配模型 key 也要能跑出"检索准不准"——这是本功能在客户还没买模型时的唯一价值：
    先证明检索层没问题，再谈生成层。生成层缺位时答案走未命中兜底，通过率必然 0，这是如实记录。"""
    c = _client(engine, tmp_path, chat_fn=None)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert run["status"] == "done" and run["passed"] == 0
    assert run["metrics"]["hit_rate"] == 1.0 and run["metrics"]["mrr"] == 1.0
    item = c.get(f"/api/v1/eval/runs/{run['id']}").json()["items"][0]
    assert "没有相关内容" in item["answer"]      # 未命中兜底话术（配置中心可改）
    assert item["error"] is None                # 没有模型不是错误，是如实降级


def test_single_item_failure_is_recorded_and_run_completes(client, engine, tmp_path):
    """单题失败不中断整轮评测，且失败原因落库（stage0 教训：
    一轮 17/20 里三题败在生成调用而非检索——报告要能直接区分这两类）。"""
    def boom(query, hits):
        raise RuntimeError("模型 403 了")

    c = _client(engine, tmp_path, chat_fn=boom)
    login(c, *ADMIN)
    c.post("/api/v1/kb", json={"name": "空"})
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert run["status"] == "done" and run["passed"] == 0
    item = c.get(f"/api/v1/eval/runs/{run['id']}").json()["items"][0]
    assert "RuntimeError: 模型 403 了" in item["error"]
    assert item["checks"]["retrieval"] is True     # 检索过了、生成挂了——归因清晰


def test_run_eval_in_real_thread_reports_progress_then_done(engine, db, tmp_path):
    """真线程路径（生产形态）：POST 立即返回且状态可轮询。测试注入的同步 spawn 掩盖不了
    "Session 跨线程是否可用"这类问题，故这里跑一次真线程。"""
    c = _client(engine, tmp_path, spawn=None)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert run["status"] in ("running", "done")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        got = c.get(f"/api/v1/eval/runs/{run['id']}").json()
        if got["status"] != "running":
            break
        time.sleep(0.05)
    assert got["status"] == "done", got
    assert got["passed"] == 1


# ---- 报告 / 历史 / 删除 ----
def test_report_markdown_and_run_history_and_delete(client, engine):
    kb = _mk_kb_with_doc(client)
    _mk_question(client)
    run = client.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    md = client.get(f"/api/v1/eval/runs/{run['id']}/report").json()["markdown"]
    assert "# 评测报告" in md and "1/1（100%）" in md
    assert DOC_NAME in md and "生鲜不支持七天无理由退货" in md
    runs = client.get("/api/v1/eval/runs").json()
    assert [x["id"] for x in runs] == [run["id"]]        # 最近一次在前
    assert client.delete(f"/api/v1/eval/runs/{run['id']}").status_code == 204
    assert client.get(f"/api/v1/eval/runs/{run['id']}").status_code == 404
    assert client.get(f"/api/v1/eval/runs/{run['id']}/report").status_code == 404
    assert client.delete(f"/api/v1/eval/runs/{run['id']}").status_code == 404
    with Session(engine) as s:
        assert s.query(EvalItemResult).count() == 0      # 明细随运行级联清
        assert s.query(AuditLog).filter_by(action="eval_run_started").count() == 1


def test_deleting_question_keeps_historical_snapshot(client, engine):
    """金标准是"当时的尺子"：题被改被删，历史运行的明细与分数不许跟着变——
    否则回看历史等于拿今天的标准重判昨天的答案。"""
    kb = _mk_kb_with_doc(client)
    q = _mk_question(client)
    run = client.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert client.delete(f"/api/v1/eval/questions/{q['id']}").status_code == 204
    detail = client.get(f"/api/v1/eval/runs/{run['id']}").json()
    item = detail["items"][0]
    assert item["question"] == q["question"] and item["note"] == "校准记录"
    assert item["expect_all"] == ["不支持"] and item["question_id"] is None
    assert detail["metrics"]["passed"] == 1
    with Session(engine) as s:
        assert s.query(EvalQuestion).count() == 0
        assert s.query(EvalRun).count() == 1


def test_disabled_questions_are_skipped(client, engine):
    kb = _mk_kb_with_doc(client)
    _mk_question(client)
    other = _mk_question(client, "另一个问题", expect_all=("不支持",))
    client.patch(f"/api/v1/eval/questions/{other['id']}", json={"enabled": False})
    run = client.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert run["total"] == 1 and run["metrics"]["pass_rate"] == 1.0


# ---- 裁判模型（§阶段2 Ragas 侧）：软指标，永不并入通过率 ----
def _judge(question, answer, hits):
    return {"faithful": 1, "relevance": 0, "reason": "没回答问题", "model": "judge-1",
            "prompt_tokens": 100, "completion_tokens": 20}


def test_judge_scores_recorded_separately_from_pass_rate(engine, db, tmp_path):
    """裁判分单独成段：同一轮里通过率仍是确定性判据算出来的，裁判只加一节软指标。"""
    c = _client(engine, tmp_path, judge_fn=_judge)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]], "judge": True}).json()
    assert run["judge"] is True
    m = run["metrics"]
    assert (m["pass_rate"], m["hit_rate"]) == (1.0, 1.0)      # 硬指标不受裁判影响
    assert m["judge"]["judged"] == 1 and m["judge"]["scored"] == 1
    assert (m["judge"]["faithful"], m["judge"]["relevance"]) == (1, 0)
    assert m["judge"]["model"] == "judge-1"
    item = c.get(f"/api/v1/eval/runs/{run['id']}").json()["items"][0]
    assert item["judge"]["reason"] == "没回答问题" and item["judge"]["faithful"] == 1
    # 裁判也是真花钱的调用：与生成分别记一笔（同一轮共 2 条 chat 台账）
    with Session(engine) as s:
        rows = s.query(UsageRecord).filter_by(scenario="chat").all()
    assert sorted(r.model for r in rows) == ["fake-chat", "judge-1"]
    # 报告里裁判分单独一段，并写明它为什么不并入通过率
    md = c.get(f"/api/v1/eval/runs/{run['id']}/report").json()["markdown"]
    assert "## 裁判评分" in md and "faithfulness（答案是否只依据资料）：1/1（100%）" in md
    assert "裁判分**不并入通过率**" in md and "裁判：faithfulness=1 relevance=0" in md


def test_run_without_judge_flag_has_no_judge_scores(engine, db, tmp_path):
    """默认不请裁判：不花那份钱，指标里也如实留着 0/None（区分"没启用"与"判失败"）。"""
    c = _client(engine, tmp_path, judge_fn=_judge)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    assert run["judge"] is False
    assert run["metrics"]["judge"] == {"judged": 0, "scored": 0, "faithful": 0,
                                       "relevance": 0, "faithful_rate": 0.0,
                                       "relevance_rate": 0.0, "model": None}
    item = c.get(f"/api/v1/eval/runs/{run['id']}").json()["items"][0]
    assert item["judge"] is None
    with Session(engine) as s:
        assert s.query(UsageRecord).count() == 1        # 只有生成那一笔


def test_judge_requested_without_configured_model_is_400(engine, db, tmp_path):
    c = _client(engine, tmp_path)          # judge_fn 缺省 None
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    r = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]], "judge": True})
    assert r.status_code == 400 and "裁判模型" in r.json()["detail"]
    assert c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).status_code == 202


def test_judge_failure_does_not_break_deterministic_checks(engine, db, tmp_path):
    """裁判挂了只损失软指标：这道题的确定性判据与整轮评测都必须照常完成。"""
    def bad_judge(question, answer, hits):
        raise RuntimeError("裁判模型 429")

    c = _client(engine, tmp_path, judge_fn=bad_judge)
    seed_user(engine, *ADMIN, role="admin")
    login(c, *ADMIN)
    kb = _mk_kb_with_doc(c)
    _mk_question(c)
    run = c.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]], "judge": True}).json()
    assert (run["status"], run["passed"]) == ("done", 1)
    assert run["metrics"]["judge"]["judged"] == 0
    item = c.get(f"/api/v1/eval/runs/{run['id']}").json()["items"][0]
    assert item["judge"] is None and "裁判调用失败" in item["error"]


# ---- 权限：评测面是 admin 专属（金标准是"标尺"，改标尺=改结论）----
def test_eval_endpoints_require_admin(client, engine, tmp_path, db):
    kb = _mk_kb_with_doc(client)
    _mk_question(client)
    run = client.post("/api/v1/eval/runs", json={"kb_ids": [kb["id"]]}).json()
    paths = [("get", "/api/v1/eval/questions", None),
             ("post", "/api/v1/eval/questions", {"question": "q"}),
             ("patch", "/api/v1/eval/questions/1", {"note": "x"}),
             ("delete", "/api/v1/eval/questions/1", None),
             ("get", "/api/v1/eval/runs", None),
             ("post", "/api/v1/eval/runs", {}),
             ("get", f"/api/v1/eval/runs/{run['id']}", None),
             ("get", f"/api/v1/eval/runs/{run['id']}/report", None),
             ("delete", f"/api/v1/eval/runs/{run['id']}", None)]
    anon = TestClient(create_app(engine=engine, secret="k", embedder=None, chat_fn=None,
                                 upload_dir=str(tmp_path)))
    for method, path, body in paths:
        call = getattr(anon, method)
        assert call(path, **({"json": body} if body is not None else {})).status_code == 401, path
    login(client, *MEMBER)
    for method, path, body in paths:
        call = getattr(client, method)
        assert call(path, **({"json": body} if body is not None else {})).status_code == 403, path
