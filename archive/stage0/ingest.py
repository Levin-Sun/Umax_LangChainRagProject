# 阶段 0：文档入库（解析→切块→向量化→写 pgvector）
import sys
from pathlib import Path

import pg8000.native

sys.stdout.reconfigure(encoding="utf-8")

import config
import embed_provider


def read_docs() -> dict[str, str]:
    docs = {}
    for p in sorted(config.DOCS_DIR.glob("*.txt")):
        docs[p.name] = p.read_text(encoding="utf-8").strip()
    print(f"读取文档 {len(docs)} 份，来自 {config.DOCS_DIR}")
    return docs


def chunk_text(text: str) -> list[str]:
    """按段落合并切块：相邻段落拼到目标长度为止，超长段落按句切分。"""
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        candidate = (buf + "\n" + para).strip() if buf else para
        if len(candidate) <= config.CHUNK_TARGET or not buf:
            buf = candidate
        else:
            chunks.append(buf)
            buf = para
        # 单段超长（罕见）按句号硬切
        while len(buf) > config.CHUNK_TARGET * 2:
            cut = buf.rfind("。", 0, config.CHUNK_TARGET * 2)
            cut = cut if cut > 0 else config.CHUNK_TARGET * 2
            chunks.append(buf[: cut + 1])
            buf = buf[cut + 1 :]
    if buf:
        chunks.append(buf)
    # 过短小块并入前一块
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < config.CHUNK_MIN:
            merged[-1] = merged[-1] + "\n" + c
        else:
            merged.append(c)
    return merged


def embed(texts: list[str]) -> list[list[float]]:
    """统一嵌入入口：按 EMBED_PROVIDER 走百炼 API 或本地模型。"""
    return embed_provider.embed(texts)


def main() -> None:
    no_embed = "--no-embed" in sys.argv
    docs = read_docs()
    rows: list[tuple[str, int, str]] = []
    for name, text in docs.items():
        for idx, c in enumerate(chunk_text(text)):
            rows.append((name, idx, c))
    print(f"切块完成：{len(rows)} 块（平均 {sum(len(r[2]) for r in rows) // len(rows)} 字/块）")

    vectors: list[list[float]] | None = None
    if no_embed:
        print("跳过向量化（纯 BM25 基线模式）")
    else:
        provider = "本地 sentence-transformers" if config.EMBED_PROVIDER == "local" else f"百炼 {config.EMBEDDING_MODEL}"
        print(f"嵌入提供方：{provider} ...")
        vectors = embed([r[2] for r in rows])
        print(f"向量维度：{len(vectors[0])}")

    con = pg8000.native.Connection(
        host=config.PG["host"], port=config.PG["port"], user=config.PG["user"],
        password=config.PG["password"], database=config.PG["database"],
    )
    con.run("CREATE EXTENSION IF NOT EXISTS vector")
    con.run("DROP TABLE IF EXISTS chunks")
    dim = len(vectors[0]) if vectors else config.EMBEDDING_DIM
    con.run(
        f"CREATE TABLE chunks (id serial primary key, doc_name text, chunk_index int,"
        f" content text, embedding vector({dim}))"
    )
    for i, ((name, idx, content), vec) in enumerate(zip(rows, vectors or [None] * len(rows))):
        con.run(
            "INSERT INTO chunks (doc_name, chunk_index, content, embedding)"
            " VALUES (:name, :idx, :content, CAST(:emb AS vector))",
            name=name, idx=idx, content=content,
            emb="[" + ",".join(f"{x:.6f}" for x in vec) + "]" if vec else None,
        )
    count = con.run("SELECT count(*) FROM chunks")[0][0]
    con.close()
    print(f"入库完成：chunks 表 {count} 行 → {config.PG['host']}:{config.PG['port']}/{config.PG['database']}")


if __name__ == "__main__":
    main()
