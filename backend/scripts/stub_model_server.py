#!/usr/bin/env python
"""本地 OpenAI 兼容桩服务（开发/自测用，**不是产品组件**）。

为什么需要它：没有模型 key 时，"网关路由 → BYO-key 解密 → 真实 HTTP 调用 → 用量台账 → 评测"
这整条链路照样要能被验证到。桩服务给的是真实 HTTP + 真实 OpenAI 协议（/chat/completions、
/embeddings），只是模型本身很笨：它只从【资料】里挑一段最相关的原文抄出来当答案。

所以用它跑出的分数**衡量的是检索与链路**（检索准不准、引用对不对、能不能端到端跑通），
不是模型的语言能力。填上真 key 后同一条链路即真模型跑分，无需改一行代码。

用法：
    python scripts/stub_model_server.py --port 8099
然后在后台「模型」页登记：
    scenario=chat / base_url=http://127.0.0.1:8099/v1 / model_name=stub-chat-1 / api_key=任意
"""
import argparse
import json
import math
import re
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM_DEFAULT = 1024
CITE = "\n\n[{}]"          # 答案末尾标来源：与真模型的带引用生成同构，parse_citations 吃这个格式
NO_HIT = "资料里没有相关内容，无法回答。"
FILE_RE = re.compile(r"\[(\d+)\]\s*（来源：(?P<doc>.*?)）\s*\n(?P<body>.*)", re.S)


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def pick_hit(question: str, blocks: list[tuple[int, str, str]]) -> tuple[int, str, str] | None:
    """挑与问题最相关的资料块（字符二元组重合度）——故意做得朴素：
    检索质量本来就该由检索层负责，让生成层去"理解"等于帮检索遮丑。
    一块都算不出重合时退到首位资料（真模型拿到资料也不会说"无关"就罢手）。
    """
    if not blocks:
        return None
    q = _bigrams(question)
    best, best_score = None, 0.0
    for n, doc, body in blocks:
        score = len(q & _bigrams(body)) / (1 + math.log(1 + len(body)))
        if score > best_score:
            best, best_score = (n, doc, body), score
    return best or blocks[0]


def _parse_user_text(messages: list[dict]) -> tuple[str, list[tuple[int, str, str]]]:
    """从提示词里还原【资料】块与问题——产品侧的 build_user_prompt 格式是唯一的输入契约。"""
    text = ""
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):        # 多模态（传图）：取其中文本部分
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        if isinstance(c, str) and "【资料】" in c:
            text = c
    corpus, _, question = text.partition("\n\n【问题】")
    corpus = corpus.partition("【资料】")[2]     # 去掉标题行，否则首块带前缀匹配不上
    blocks = []
    for chunk in corpus.split("\n\n"):
        m = FILE_RE.match(chunk.strip())
        if m:
            blocks.append((int(m.group(1)), m.group("doc"), m.group("body").strip()))
    return question.strip(), blocks


def chat_answer(messages: list[dict]) -> str:
    question, blocks = _parse_user_text(messages)
    if not blocks:
        return NO_HIT
    hit = pick_hit(question, blocks)
    if hit is None:
        return NO_HIT
    n, _doc, body = hit
    return f"根据资料{n}，原文如下：{body}" + CITE.format(n)


def embed_text(text: str, dim: int = DIM_DEFAULT) -> list[float]:
    """确定性伪嵌入：字符二元组哈希到 dim 维再归一化。同主题文本余弦相似度确实更高
    （共享字符多），但远不如真嵌入模型——够用来验证"向量这一路通不通"。"""
    vec = [0.0] * dim
    for bg, w in _bigrams(text):
        vec[zlib.crc32(bg.encode("utf-8")) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # 静音：评测一轮会打几十条，日志留给调用方
        pass

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:                      # noqa: N802（http.server 的命名约定）
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "stub-chat-1", "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:                     # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "invalid json"}})
            return
        if self.path.rstrip("/").endswith("/chat/completions"):
            answer = chat_answer(body.get("messages") or [])
            prompt = sum(len(str(m.get("content"))) for m in (body.get("messages") or []))
            self._json(200, {
                "id": "chatcmpl-stub", "object": "chat.completion",
                "created": 0, "model": body.get("model") or "stub-chat-1",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer}}],
                # token 数按字符数估：桩服务不真分词，但台账要有真实形状的数字可核
                "usage": {"prompt_tokens": prompt // 2, "completion_tokens": len(answer) // 2,
                          "total_tokens": (prompt + len(answer)) // 2}})
        elif self.path.rstrip("/").endswith("/embeddings"):
            inputs = body.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            dim = int(body.get("dimensions") or DIM_DEFAULT)
            self._json(200, {
                "object": "list", "model": body.get("model") or "stub-embed-1",
                "data": [{"object": "embedding", "index": i, "embedding": embed_text(t, dim)}
                         for i, t in enumerate(inputs)],
                "usage": {"prompt_tokens": sum(len(t) for t in inputs) // 2, "total_tokens": 0}})
        else:
            self._json(404, {"error": {"message": "not found"}})


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenAI 兼容桩服务（仅开发/自测）")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"桩模型服务已启动：http://{args.host}:{args.port}/v1"
          f"（chat + embeddings；答案=抄资料，仅供链路验证）", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
