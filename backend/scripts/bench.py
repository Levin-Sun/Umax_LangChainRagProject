#!/usr/bin/env python
"""性能基线：把"感觉慢了"变成可比的数字（评审遗留：此前只量过网关客户端复用那一处）。

量的是什么、为什么是这些：**只量我们能控制的那部分**——检索内核、切块入库、并发吞吐。
模型调用（真 key、上游抖动、按 token 计费）不在其中：它变慢不是我们的代码变慢，
把它混进来只会让基线不可比。所以本脚本全程用假 embedder / 不调模型，不花 key、不依赖网络，
在任何机器上都能重复跑。

口径与诚实标注：
- 每项取 **best-of-N**（本机噪声大，最小值最能反映"代码本身有多快"），同时报 p95 供参考；
- 数字是**本机**的，绝对值只在同一台机器上可横向比较；跨机器的信号是**随语料规模的曲线**；
- `--check` 用倍率阈值（默认 1.5×）与本仓入库的基线段比，用来发现"这次改动让它慢了一截"。
  没有把它接进 CI 主门禁：共享 runner 上的耗时是噪声，误红比漏报更糟（见 docs/PERF.md）。

用法：
    ../.venv/bin/python scripts/bench.py                 # 跑一遍，打印表格
    ../.venv/bin/python scripts/bench.py --check         # 与 docs/perf-baseline.json 比，超阈值则退出码 1
    ../.venv/bin/python scripts/bench.py --write         # 用本次结果更新基线文件
    ../.venv/bin/python scripts/bench.py --sizes 500,2000
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
from sqlalchemy import create_engine, delete, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import run_migrations  # noqa: E402
from app.models import Chunk, Document, KnowledgeBase  # noqa: E402
from app.services.chunking import chunk_text  # noqa: E402
from app.services.ingest import ingest_document  # noqa: E402
from app.services.retrieval import _bm25_ranking, _load_chunks, _vector_ranking, retrieve  # noqa: E402
from app.services.settings import SettingsStore  # noqa: E402

DIM = 1024
BENCH_DB = "umaxrag_bench"
BASELINE = Path(__file__).resolve().parents[2] / "docs" / "perf-baseline.json"

# 语料用可复现的伪中文（固定种子）：分词器要真跑，但内容不必有意义
VOCAB = ("知识库 检索 向量 切块 授权 审计 配额 模型 网关 重排 评测 会话 文档 图片 索引 "
         "回答 引用 召回 融合 精度 成本 客户 部署 升级 备份 日志 并发 延迟 吞吐 基线").split()


class FakeEmbedder:
    """确定性假向量：1024 维、无网络。**距离计算量与真向量一致**（PG 照样算满 1024 维），
    所以向量路的耗时是真实的；只有"上游 HTTP 往返"被省掉——那部分本来也不该进这个基线。"""

    def __init__(self, dim: int = DIM, seed: int = 7) -> None:
        self.dim, self._rng = dim, random.Random(seed)

    def embed(self, texts):
        out = []
        for t in texts:
            r = random.Random(hash(t) & 0xFFFF)
            out.append([r.random() for _ in range(self.dim)])
        return out


def _vec_literal(vals: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vals) + "]"


def build_corpus(engine, n_chunks: int, seed: int = 11) -> int:
    """建一个知识库 + 一篇文档 + n_chunks 个切块（直接落库，绕过解析/切块，聚焦检索）。"""
    rng = random.Random(seed)
    emb = FakeEmbedder()
    base = [rng.random() for _ in range(DIM)]
    with Session(engine) as s:
        s.execute(delete(Chunk))
        s.execute(delete(Document))
        s.execute(delete(KnowledgeBase))
        s.commit()
        kb = KnowledgeBase(tenant_id="default", name="bench", description="性能基线语料")
        s.add(kb)
        s.flush()
        doc = Document(tenant_id="default", kb_id=kb.id, name="bench.txt", status="ready")
        s.add(doc)
        s.flush()
        rows = []
        for i in range(n_chunks):
            body = "".join(rng.choice(VOCAB) for _ in range(rng.randint(20, 40)))
            # 向量：基准向量 + 少量维度扰动——生成快，但 PG 的距离计算量不变（1024 维照算）
            vec = list(base)
            for _ in range(32):
                vec[rng.randrange(DIM)] = rng.random()
            rows.append({"tenant_id": "default", "document_id": doc.id, "kb_id": kb.id,
                         "chunk_index": i, "content": body, "meta": "{}",
                         "embedding": _vec_literal(vec)})
        # 逐行 executemany：一次塞 8000×1024 浮点的文本体，比 ORM 逐对象快一个量级
        s.execute(text("INSERT INTO chunks (tenant_id, document_id, kb_id, chunk_index, "
                       "content, meta, embedding) VALUES (:tenant_id, :document_id, :kb_id, "
                       ":chunk_index, :content, CAST(:meta AS jsonb), CAST(:embedding AS vector))"),
                  rows)
        s.commit()
        return kb.id


def timeit(fn, repeat: int) -> tuple[float, float]:
    """返回 (best_ms, p95_ms)。先热身一次，避免把冷启动算进基线。"""
    fn()
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return round(min(samples), 2), round(p95, 2)


def bench_sizes(engine, sizes: list[int], repeat: int, query: str) -> dict:
    emb = FakeEmbedder()
    out: dict = {}
    for n in sizes:
        kb_id = build_corpus(engine, n)
        with Session(engine) as s:
            chunk_ids = [r[0] for r in s.execute(
                text("SELECT id FROM chunks WHERE kb_id = :k"), {"k": kb_id})]
            qvec = emb.embed([query])[0]

            def phase_load():
                _load_chunks(s, [kb_id])

            def phase_bm25():
                _bm25_ranking(_load_chunks(s, [kb_id]), query, 10)

            def phase_vec():
                _vector_ranking(s, qvec, [kb_id], 10, 0.15)

            def end_to_end():
                retrieve(s, query, embedder=emb, kb_ids=[kb_id],
                         recall_k=10, top_k=5, min_sim=0.15)

        load_ms, load_p95 = timeit(phase_load, repeat)
        bm25_ms, bm25_p95 = timeit(phase_bm25, repeat)
        vec_ms, vec_p95 = timeit(phase_vec, repeat)
        e2e_ms, e2e_p95 = timeit(end_to_end, repeat)
        out[str(n)] = {"chunks": len(chunk_ids), "end_to_end_ms": e2e_ms, "end_to_end_p95_ms": e2e_p95,
                       "load_chunks_ms": load_ms, "bm25_ms": bm25_ms, "bm25_p95_ms": bm25_p95,
                       "vector_ms": vec_ms, "vector_p95_ms": vec_p95,
                       # bm25 含 load：单看"分词+建索引"要减掉 load
                       "tokenize_index_ms": round(bm25_ms - load_ms, 2),
                       "hits": len(retrieve(Session(engine), query, embedder=emb, kb_ids=[kb_id],
                                            recall_k=10, top_k=5, min_sim=0.15))}
        print(f"  语料 {n:>6} 切块：端到端 {e2e_ms:8.2f} ms（p95 {e2e_p95:8.2f}）"
              f" ｜取块 {load_ms:7.2f} ｜分词+建 BM25 {bm25_ms - load_ms:7.2f} ｜向量 {vec_ms:7.2f}")
    return out


def bench_concurrency(engine, n_chunks: int, workers: int, per_worker: int, query: str) -> dict:
    """并发打真检索引擎（无模型调用）：看的是**有没有锁/连接竞争**，不是绝对吞吐。

    单独建一个库、不加锁地并发读，正是 retrieve 的真实形态（它自己不写库）；
    若这里随并发数陡降，说明有共享资源被串行化（连接池、行锁、GIL 里的分词）。
    """
    kb_id = build_corpus(engine, n_chunks)
    emb = FakeEmbedder()
    lat: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(workers)

    def worker():
        with Session(engine) as s:
            barrier.wait()          # 同时起跑，避免被启动顺序摊平
            local = []
            for _ in range(per_worker):
                t0 = time.perf_counter()
                retrieve(s, query, embedder=emb, kb_ids=[kb_id], recall_k=10, top_k=5, min_sim=0.15)
                local.append((time.perf_counter() - t0) * 1000)
            with lock:
                lat.extend(local)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    lat.sort()
    res = {"workers": workers, "requests": len(lat), "wall_s": round(wall, 3),
           "throughput_qps": round(len(lat) / wall, 2),
           "p50_ms": round(statistics.median(lat), 2),
           "p95_ms": round(lat[int(len(lat) * 0.95)], 2),
           "max_ms": round(lat[-1], 2)}
    print(f"  并发 {workers} 线程 × {per_worker} 次（语料 {n_chunks}）："
          f"吞吐 {res['throughput_qps']} q/s ｜ p50 {res['p50_ms']} ms ｜ p95 {res['p95_ms']} ms")
    return res


def bench_ingest(engine, repeat: int) -> dict:
    """入库一篇 ~40KB 的中文文档（解析→切块→假向量→写库）：这是"上传要等多久"的数字。"""
    s = get_settings()
    store = SettingsStore(engine, s)
    para = "".join(random.Random(3).choice(VOCAB) for _ in range(60))
    raw = ("\n".join(para for _ in range(60))).encode("utf-8")   # ≈ 40KB、60 段
    emb = FakeEmbedder()

    def once():
        with Session(engine) as session:
            kb = session.query(KnowledgeBase).first()
            doc = Document(tenant_id="default", kb_id=kb.id, name="ingest.txt", status="pending")
            session.add(doc)
            session.commit()
            ingest_document(session, doc, raw, embedder=emb, **store.chunk_params(kb))
            return session.query(Chunk).filter_by(document_id=doc.id).count()

    n_chunks = once()
    ms, p95 = timeit(once, repeat)
    print(f"  入库 {len(raw) // 1024} KB 文本 → {n_chunks} 块：{ms} ms（p95 {p95}）"
          f"（含解析/切块/假向量/写库；不含模型 HTTP）")
    return {"input_kb": len(raw) // 1024, "chunks": n_chunks, "ms": ms, "p95_ms": p95}


def main() -> int:
    ap = argparse.ArgumentParser(description="性能基线（假 embedder，不调模型）")
    ap.add_argument("--sizes", default="500,2000,8000", help="语料规模（切块数），逗号分隔")
    ap.add_argument("--repeat", type=int, default=3, help="每项重复次数（取 best，报 p95）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--per-worker", type=int, default=5)
    ap.add_argument("--check", action="store_true", help="与基线比，超倍率阈值则退出码 1")
    ap.add_argument("--write", action="store_true", help="用本次结果更新基线文件")
    ap.add_argument("--tolerance", type=float, default=1.5, help="--check 的倍率容忍")
    args = ap.parse_args()

    s = get_settings()
    name = os.environ.get("BENCH_DB", BENCH_DB)
    with psycopg.connect(s.psycopg_url("postgres"), autocommit=True) as con:
        con.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        con.execute(f'CREATE DATABASE "{name}"')
    engine = create_engine(s.sqlalchemy_url(name))
    run_migrations(engine)      # 顺手验证迁移在全新库上能一次建齐（Alembic 已接管建表）

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    query = "向量 检索 切块 授权"      # 语料里的词，保证两路都能召回
    print(f"机器 {sys.platform}/{os.uname().machine} ｜ Python {sys.version.split()[0]} "
          f"｜ 库 {name} ｜ best-of-{args.repeat}")
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0], "platform": f"{sys.platform}/{os.uname().machine}",
        "repeat": args.repeat, "embedder": "fake-1024d（无网络）",
        "note": "绝对值仅本机可比；跨机器看随语料规模的曲线与各项占比",
    }
    print("[检索：随语料规模]")
    result["retrieval"] = bench_sizes(engine, sizes, args.repeat, query)
    print("[并发：真检索引擎、无模型调用]")
    result["concurrency"] = bench_concurrency(engine, sizes[len(sizes) // 2],
                                              args.workers, args.per_worker, query)
    print("[入库：解析→切块→假向量→写库]")
    result["ingest"] = bench_ingest(engine, args.repeat)
    engine.dispose()

    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"\n已更新基线 {BASELINE}")
    if args.check:
        if not BASELINE.exists():
            print(f"\n没有基线文件 {BASELINE}：先跑一次 --write")
            return 1
        old = json.loads(BASELINE.read_text(encoding="utf-8"))
        bad = []
        for n, cur in result["retrieval"].items():
            prev = old.get("retrieval", {}).get(n)
            if not prev:
                continue
            for key in ("end_to_end_ms", "bm25_ms", "vector_ms"):
                if cur[key] > prev[key] * args.tolerance:
                    bad.append(f"语料 {n} 的 {key}: {prev[key]} → {cur[key]} ms"
                               f"（>{args.tolerance}×）")
        prev_ing = old.get("ingest", {}).get("ms")
        if prev_ing and result["ingest"]["ms"] > prev_ing * args.tolerance:
            bad.append(f"入库: {prev_ing} → {result['ingest']['ms']} ms（>{args.tolerance}×）")
        if bad:
            print("\n❌ 相对基线变慢：")
            for b in bad:
                print("  ", b)
            return 1
        print(f"\n✅ 相对基线无超阈值退化（容忍 {args.tolerance}×）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
