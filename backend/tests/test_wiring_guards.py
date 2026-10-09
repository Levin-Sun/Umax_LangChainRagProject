# 装配漂移守卫（静态）：这类缝**单测天然看不见**——单测跑在宿主机、走的是 backend 那份装配，
# 而线上还有第二份（ARQ worker）和第三份（compose 给 worker 的环境变量）。
#
# 真机踩中（2026-10-09，异步档第一次真跑）：worker 的 `_startup` 自己造了
# `BailianEmbedder(api_key=s.dashscope_api_key)`，于是"按推荐方式在后台登记 embedding 模型
# （.env 不填 key）"的客户一切到异步档，**每篇文档都入库失败**：
#   ❌ 入库失败：LocalProtocolError: Illegal header value b'Bearer '
# 而同步档 12/12 全绿。两条链各自都对，一对就露馅——所以这里把"只有一份装配"钉成断言。
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def _code(path: Path) -> str:
    """只留代码：注释里提到类名是解释，不是装配——守卫不该被自己的注释绊倒
    （本文件第一次跑就绊了一次，那条断言确实有牙）。"""
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_worker_shares_backend_embedder_wiring():
    """worker 不许自造 embedder：必须走 compose.make_embedder（否则后台登记的模型对它无效）。"""
    src = _code(BACKEND / "app" / "worker.py")
    assert "BailianEmbedder" not in src, "worker 出现 BailianEmbedder＝又分家了一份装配"
    assert "make_embedder" in src


def test_backend_and_worker_use_the_same_helper():
    """两份装配的收敛点：backend 与 worker 都调 make_embedder。"""
    for name in ("main.py", "worker.py"):
        src = (BACKEND / "app" / name).read_text(encoding="utf-8")
        assert "make_embedder" in src, f"{name} 没走共享装配"


def test_worker_service_gets_gateway_secret_in_compose():
    """compose 的 worker 服务也要有 GATEWAY_SECRET：没有它，网关那条路在 worker 里根本起不来。"""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker_block = compose.split("\n  worker:")[1]
    assert "GATEWAY_SECRET" in worker_block, "worker 服务缺 GATEWAY_SECRET"
