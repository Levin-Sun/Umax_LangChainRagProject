#!/usr/bin/env bash
# 一键门禁：把 CI 里那几条在本地按同样顺序跑一遍。
#
# 为什么要有它：门禁命令一直写在文档里、也一直是对的，但**没有东西强制它发生**——
# 全靠人记得手敲，漏跑一条不会有人知道。CI（.github/workflows/ci.yml）是第一选择；
# 这个脚本给"还没配 CI / 想推之前先自查"的场景兜底，且不依赖任何凭据。
#
# 用法：bash scripts/run_gates.sh        （在仓库根目录执行；需先建好 .venv 与 test 库）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=${PY:-$ROOT/.venv/bin/python}
fail=0
step() { printf '\n=== %s ===\n' "$1"; }
run()  { if "$@"; then echo "  ✅ $*"; else echo "  ❌ $*"; fail=1; fi; }

# pytest.ini 在 backend/ 下（testpaths/pythonpath 都在那儿）——必须在该目录里跑
# 覆盖率棘轮：--cov-fail-under 卡在基线之下=谁把测试删了/绕过就红；
# 与用例同一轮跑（不额外多跑一遍），报告里带缺失行号，方便看"哪些分支从没被执行过"
COV_MIN=${COV_MIN:-93}
step "后端用例 + 覆盖率（含 RBAC/审计/权限/评测/重建/并发全套回归）"
(cd "$ROOT/backend" && run "$PY" -m pytest -q --cov=app --cov-report=term-missing \
  --cov-fail-under="$COV_MIN")
step "契约 fuzz（schemathesis：声明与实现必须一致）"
(cd "$ROOT/backend" && run "$PY" -m pytest -m contract -q)
step "契约新鲜度（改了端点没重导 spec 即红）"
"$PY" "$ROOT/backend/scripts/export_openapi.py" >/dev/null
run git diff --exit-code -- contracts/openapi.json
step "SDK 生成物新鲜度（spec 变了没重生成 SDK 即红）"
(cd "$ROOT/sdk-ts" && run npm test)
step "前端用例"
(cd "$ROOT/frontend" && run npm test)
step "类型检查（含手写类型 vs 契约生成类型的耦合断言）"
(cd "$ROOT/frontend" && run npm run typecheck)
step "前端构建（走独立产物目录，不碰 dev 的 .next）"
(cd "$ROOT/frontend" && run npm run build:verify)

if [ "$fail" -ne 0 ]; then printf '\n有门禁未通过 ❌\n'; exit 1; fi
printf '\n全部门禁通过 ✅\n'
