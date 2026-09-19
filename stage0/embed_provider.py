# 嵌入提供方：百炼 API（bailian）或本地 sentence-transformers（local）
import time

import httpx

import config

_model = None  # 本地模型懒加载缓存

RETRY_BACKOFF = [2, 5]  # 应对专属端点间歇性 403 / 断连


def _local_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        print(f"加载本地嵌入模型 {config.LOCAL_EMBED_MODEL} ...")
        _model = SentenceTransformer(config.LOCAL_EMBED_MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    if config.EMBED_PROVIDER == "local":
        return [v.tolist() for v in _local_model().encode(texts, normalize_embeddings=True)]
    out: list[list[float]] = []
    for i in range(0, len(texts), 10):
        batch = texts[i : i + 10]
        last_err: Exception | None = None
        for attempt, wait in enumerate([0] + RETRY_BACKOFF, 1):
            time.sleep(wait)
            try:
                resp = httpx.post(
                    f"{config.COMPAT_BASE}/embeddings",
                    headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
                    json={"model": config.EMBEDDING_MODEL, "input": batch,
                          "dimensions": config.EMBEDDING_DIM, "encoding_format": "float"},
                    timeout=60,
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                out.extend(d["embedding"] for d in data)
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"⚠️ 嵌入调用失败（第{attempt}次，{e.__class__.__name__}），退避后重试")
        if last_err is not None:
            raise last_err
    return out
