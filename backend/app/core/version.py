# 代码版本印记：/health 用它回答"进程跑的到底是哪份代码"。
# 独立成模块是为了让 /health 端点（app/api/routers/system.py）能取到它——
# 原先它定义在 main.py 里，端点在 main 内是闭包所以能直接用；拆 router 后
# 端点模块不能再反过来 import main（循环），版本印记也就该有自己的家。
from pathlib import Path


def _build_commit() -> str:
    """当前代码版本：容器部署可用 BUILD_COMMIT 注入（镜像内无 .git），开发态直接问 git。"""
    import os
    import subprocess

    env = (os.environ.get("BUILD_COMMIT") or "").strip()
    if env:
        return env[:40]
    try:
        repo = Path(__file__).resolve().parents[3]
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if not sha:
            return "unknown"
        # 工作区有未提交改动时标 -dirty：排查"改了不生效"时不该把未提交的改动误当成已生效
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                               capture_output=True, text=True, timeout=3).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:
        return "unknown"


BUILD_COMMIT = _build_commit()
