# 契约化 + SDK 化落地方案（任务 7 前端前置）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 FastAPI 后端升级为"契约先行"：`/api/v1` 版本冻结、OpenAPI spec 入库为唯一事实源、schemathesis 契约 fuzz + 漂移守护，并生成 TS 类型化客户端供前端直接消费。

**Architecture:** 后端所有端点统一 `/api/v1` 前缀并显式声明全部错误响应（404/415/422/503/400），导出的 `contracts/openapi.json` 入库；漂移测试保证"改了代码没更新 spec 就红"，schemathesis 保证"实现不符合 spec 就红"。前端不写裸 fetch，只用 `sdk-ts`（openapi-typescript 生成类型 + openapi-fetch 运行时）。

**Tech Stack:** FastAPI（已有）、pytest + schemathesis（ASGI 内嵌调用，零外呼零起服）、openapi-typescript + openapi-fetch（Node 24 可用）。

**Spec:** 本文件即方案；需求出处：`产品需求与开发方案.md` §2C（后台换模型/BYO-key/用量台账）、§6.5-3（模型网关已完成）、阶段 1 里程碑"部署→传文档→问答→后台换模型→看用量"闭环。2026-09-19 会话拍板：一期只做契约层 + TS SDK，Python SDK 押后二期开放 API。

## Global Constraints

- 全程 TDD：先红后绿，每任务一个完整绿灯检查点
- 数据库不 mock：真 PG 容器 + `umaxrag_test`；远程 API 用 MockTransport/假实现，日常回归零外呼
- 提交节奏：每任务完成一个提交；push 需用户口令
- 版本前缀精确值：`/api/v1`（health 也在其内，一期无线上用户，直接切断旧路径，不留兼容层）
- spec 文件精确路径：`contracts/openapi.json`（仓库根），导出需 `sort_keys=True, indent=2, ensure_ascii=False` 保证 diff 稳定
- 前端消费方式：同源反代 `/api/v1/* → backend:8000`，无 CORS

## File Structure

```
contracts/openapi.json                 # 新增：入库的 API 契约（生成物，禁止手改）
backend/app/main.py                    # 修改：路由前缀 /api/v1 + responses 显式声明
backend/scripts/export_openapi.py      # 新增：导出生成物
backend/tests/test_contract_drift.py   # 新增：漂移守护（committed == 现生成）
backend/tests/test_contract.py         # 新增：schemathesis 契约 fuzz
backend/tests/test_api.py 等 6 个      # 修改：路径机械替换 /api/ → /api/v1/
sdk-ts/package.json                    # 新增：TS SDK 包（gen + tsc 校验）
sdk-ts/src/schema.d.ts                 # 生成物：openapi-typescript 输出
sdk-ts/src/client.ts                   # 新增：createClient 薄封装（唯一手写文件）
README.md                              # 修改：契约工作流说明
```

---

### Task 1: `/api/v1` 统一 + spec 入库 + 漂移守护

**Files:**
- Modify: `backend/app/main.py`（全部 `@app.<method>("/api/...")` 装饰器）
- Modify: `backend/tests/test_api.py`、`test_models_api.py`、`test_queue.py`、`test_smoke_api.py`（若含 /api 路径）
- Create: `backend/scripts/export_openapi.py`、`contracts/openapi.json`、`backend/tests/test_contract_drift.py`

**Interfaces:**
- Produces: 全部 HTTP 端点移至 `/api/v1/...`；`python scripts/export_openapi.py` 重新生成 `contracts/openapi.json`；`test_spec_matches_committed` 守护。

- [ ] **Step 1: 测试路径改红**

```bash
cd backend
sed -i -E 's#("/api/|f"/api/)#"\1v1/#g; s#"/api/v1/#"/api/v1/#g' tests/*.py
```
上命令若转义出错则手工改：仅把字符串字面量里的 `"/api/` 与 `f"/api/` 前缀替换为 `"/api/v1/`、`f"/api/v1/`。
Run: `../.venv/Scripts/python.exe -m pytest tests/test_api.py -x -q`
Expected: FAIL —— 404（旧代码还挂在 /api/）

- [ ] **Step 2: main.py 前缀实现**

```bash
sed -i -E 's#(@app\.(get|post|patch|delete)\(")/api/#\1/api/v1/#' app/main.py
```
Run: `../.venv/Scripts/python.exe -m pytest -q`
Expected: 80 passed（全部绿灯=迁移完成）

- [ ] **Step 3: 写漂移守护测试（红）**

```python
# backend/tests/test_contract_drift.py
# 契约即事实源：改端点必须同步重导 spec，否则此测试红——diff openapi.json 即评审面
import json
from pathlib import Path

from app.main import create_app

SPEC = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def _current_spec() -> str:
    app = create_app(engine=None)  # openapi() 不触库，engine 仅请求期使用
    return json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_spec_matches_committed():
    assert SPEC.read_text(encoding="utf-8") == _current_spec()
```

- [ ] **Step 4: 运行确认红**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_contract_drift.py -q`
Expected: FAIL —— FileNotFoundError（contracts/openapi.json 不存在）

- [ ] **Step 5: 导出脚本 + 首次生成**

```python
# backend/scripts/export_openapi.py
"""重新生成 contracts/openapi.json（唯一合法入口，禁止手改生成物）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_app  # noqa: E402

spec = create_app(engine=None).openapi()
out = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
out.write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
               encoding="utf-8")
print(f"已导出 {out}（{len(spec['paths'])} 个路径）")
```

```bash
mkdir -p ../contracts && ../.venv/Scripts/python.exe scripts/export_openapi.py
../.venv/Scripts/python.exe -m pytest tests/test_contract_drift.py -q
```
Expected: 导出成功，drift 测试 PASS

- [ ] **Step 6: 全量回归 + 提交**

```bash
../.venv/Scripts/python.exe -m pytest -q   # Expected: 81 passed
git add -A ../contracts ../backend && git commit -m "stage1 契约化-1: /api/v1 版本统一 + openapi.json 入库 + 漂移守护"
```

---

### Task 2: 端点错误响应补全（为契约 fuzz 铺路）

**Files:**
- Modify: `backend/app/main.py`（各装饰器加 `responses=`，新增 `ErrorOut` 模型）
- Modify: `backend/tests/test_contract_declared.py`（新增）
- Regenerate: `contracts/openapi.json`

**Interfaces:**
- Produces: spec 中每个端点声明其全部真实状态码；`ErrorOut = {"detail": str}`。状态码映射表：

| 端点 | 需声明 |
|---|---|
| GET /api/v1/health | （无） |
| POST/GET /api/v1/kb、GET /api/v1/documents/{id}/chunks、GET /api/v1/conversations*、GET /api/v1/models、GET /api/v1/usage/summary、POST /api/v1/retrieve、POST /api/v1/chat | 默认 422 已在 |
| POST /api/v1/kb/{kb_id}/documents | 404, 415 |
| GET /api/v1/documents/{id} | 404 |
| PATCH /api/v1/documents/{id} | 404 |
| POST /api/v1/documents/{id}/reprocess | 404 |
| GET /api/v1/conversations/{id}/messages | 404 |
| POST /api/v1/models | 400, 503 |
| PATCH /api/v1/models/{id} | 400, 404, 503 |
| DELETE /api/v1/models/{id} | 503 |

- [ ] **Step 1: 写 spec 声明断言测试（红）**

```python
# backend/tests/test_contract_declared.py
# 契约 fuzz 的前提：错误码必须出现在 spec 里，否则 schemathesis 把合法 404 判成违约
from pathlib import Path

import json

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json")
                  .read_text(encoding="utf-8"))

EXPECTED = {
    ("/api/v1/kb/{kb_id}/documents", "post"): {"404", "415"},
    ("/api/v1/documents/{doc_id}", "get"): {"404"},
    ("/api/v1/documents/{doc_id}", "patch"): {"404"},
    ("/api/v1/documents/{doc_id}/reprocess", "post"): {"404"},
    ("/api/v1/conversations/{conv_id}/messages", "get"): {"404"},
    ("/api/v1/models", "post"): {"400", "503"},
    ("/api/v1/models/{model_id}", "patch"): {"400", "404", "503"},
    ("/api/v1/models/{model_id}", "delete"): {"503"},
}


def test_error_codes_declared():
    for (path, method), codes in EXPECTED.items():
        declared = set(SPEC["paths"][path][method]["responses"])
        assert codes <= declared, f"{method.upper()} {path} 缺 {codes - declared}"
```

- [ ] **Step 2: 运行确认红**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_contract_declared.py -q`
Expected: FAIL —— KeyError 或 缺码断言

- [ ] **Step 3: main.py 实现声明**

```python
class ErrorOut(BaseModel):
    detail: str


_ERR = lambda code, msg: {code: {"model": ErrorOut, "description": msg}}  # noqa: E731
```
按映射表给各装饰器追加，示例（其余同构）：

```python
@app.post("/api/v1/kb/{kb_id}/documents", status_code=201,
          responses={**_ERR(404, "知识库不存在"), **_ERR(415, "不支持的文件类型")})
@app.post("/api/v1/models", status_code=201,
          responses={**_ERR(400, "scenario 非法"), **_ERR(503, "未配置 GATEWAY_SECRET")})
@app.delete("/api/v1/models/{model_id}", status_code=204,
            responses=_ERR(503, "未配置 GATEWAY_SECRET"))
```

```bash
../.venv/Scripts/python.exe scripts/export_openapi.py
../.venv/Scripts/python.exe -m pytest -q
```
Expected: 声明测试 PASS，drift 测试在重导后 PASS，全量 82 passed

- [ ] **Step 4: 提交**

```bash
git add backend contracts && git commit -m "stage1 契约化-2: 端点错误响应显式声明进 spec"
```

---

### Task 3: schemathesis 契约 fuzz

**Files:**
- Create: `backend/tests/test_contract.py`
- Modify: `backend/requirements.txt`（+schemathesis）
- Modify: `backend/pyproject.toml` 或 pytest 配置（注册 `contract` marker）

**Interfaces:**
- Consumes: Task 1/2 的 spec 声明 + `create_app` 依赖注入（FakeEmbedder/假 chat_fn/secret，与 test_models_api 同套假件）
- Produces: `pytest -m contract` 一条命令跑全端点合规 fuzz；默认回归不带（`addopts` 保持 `-m "not smoke"`，contract 并入日常回归跑，若耗时 >90s 再降级为独立 marker）

- [ ] **Step 1: 安装并记录版本**

```bash
../.venv/Scripts/python.exe -m pip install schemathesis && ../.venv/Scripts/python.exe -m pip show schemathesis | head -2
```
requirements.txt 追加 `schemathesis>=4`。

- [ ] **Step 2: 写契约测试（先红——无 marker/依赖问题即失败面）**

```python
# backend/tests/test_contract.py
# 契约 fuzz：schemathesis 按 openapi spec 自动生成请求打 ASGI 应用（不起服务、零外呼），
# 每个响应校验状态码已声明 + body 符合 schema —— "实现偏离 spec 就红"
import pytest
import schemathesis

from app.main import create_app
from tests.test_models_api import FakeEmbedder  # 复用假件（tests 目录已在 pythonpath）
from app.core.config import get_settings

pytestmark = pytest.mark.contract


@pytest.fixture
def contract_app(engine, db, tmp_path):
    def fake_chat(query, hits):
        return {"answer": "fuzz[1]", "prompt_tokens": 1, "completion_tokens": 1}
    return create_app(engine=engine, secret="contract-secret",
                      embedder=FakeEmbedder(get_settings().embedding_dim),
                      chat_fn=fake_chat, upload_dir=str(tmp_path))


schema = schemathesis.pytest.from_fixture("contract_app")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```
若安装版的 pytest 集成 API 与上述不符（`schemathesis.pytest.from_fixture` 不存在），按
`python -c "import schemathesis; help(schemathesis.pytest)"` 输出的当期官方写法调整，语义不变：
spec 驱动、ASGI 内嵌、call_and_validate。

- [ ] **Step 3: 注册 marker + 跑通**

pytest 配置 `markers` 加 `contract: 契约 fuzz`。
Run: `../.venv/Scripts/python.exe -m pytest tests/test_contract.py -q`
Expected: 全 PASS。若暴露真实违约（未声明状态码/字段漂移）：那是 fuzz 抓到的 bug——修实现或补声明，禁止放宽校验参数。

- [ ] **Step 4: 全量回归计时 + 提交**

```bash
../.venv/Scripts/python.exe -m pytest -q   # 记录耗时；>90s 则把 contract 挪进 addopts 排除并保留 `-m contract` 手动跑
git add backend && git commit -m "stage1 契约化-3: schemathesis 契约 fuzz 进回归"
```

---

### Task 4: TS 类型化客户端 `sdk-ts`

**Files:**
- Create: `sdk-ts/package.json`、`sdk-ts/tsconfig.json`、`sdk-ts/src/client.ts`
- Generate: `sdk-ts/src/schema.d.ts`

**Interfaces:**
- Consumes: `contracts/openapi.json`
- Produces: `createApiClient(baseUrl?)` → openapi-fetch 强类型 client（`client.GET("/kb/{kb_id}/documents", {path, body})` 编译期校验 URL/参数/响应）；任务 7 前端 `import { api } from "@umax/sdk-ts"` 使用

- [ ] **Step 1: 初始化包（npm registry 若不通，走 `npm config set proxy http://127.0.0.1:7897` 或镜像）**

```bash
mkdir -p sdk-ts/src && cd sdk-ts
npm init -y && npm i -D openapi-typescript typescript && npm i openapi-fetch
```
`package.json` 覆写 scripts：

```json
{
  "name": "@umax/sdk-ts", "type": "module", "main": "src/client.ts",
  "scripts": {
    "gen": "openapi-typescript ../contracts/openapi.json -o src/schema.d.ts",
    "typecheck": "tsc --noEmit",
    "test": "npm run gen && npm run typecheck"
  }
}
```
`tsconfig.json`: `{"compilerOptions": {"strict": true, "module": "esnext", "moduleResolution": "bundler", "target": "es2022", "noEmit": true}, "include": ["src"]}`

- [ ] **Step 2: 手写唯一源文件**

```typescript
// sdk-ts/src/client.ts
// 契约驱动生成：schema.d.ts 来自 openapi.json（npm run gen），手写只有这个薄封装
import createClient from "openapi-fetch";
import type { paths } from "./schema.js";

export function createApiClient(baseUrl = "/api/v1") {
  return createClient<paths>({ baseUrl });
}
export type { paths };
```

- [ ] **Step 3: 生成 + 编译校验（tsc 即类型层测试：spec 与调用面不匹配就红）**

```bash
npm test
```
Expected: gen 成功、tsc 0 错误

- [ ] **Step 4: .gitignore + 提交**

根 `.gitignore` 追加 `sdk-ts/node_modules/`（`schema.d.ts` 是生成物但入库，便于审阅契约变更面）。

```bash
git add sdk-ts .gitignore && git commit -m "stage1 契约化-4: openapi-typescript 生成 TS 类型化客户端"
```

---

### Task 5: 工作流文档 + 可选破坏性 diff

**Files:**
- Modify: `README.md`、`产品需求与开发方案.md`（§6.5 追加一条已完成记录）

- [ ] **Step 1: README 增加"契约工作流"小节**

内容（实写，非占位）：改端点四步——①先改/加测试（红）②实现 ③`cd backend && python scripts/export_openapi.py` ④`pytest`+`cd sdk-ts && npm test`；前端一律经 `@umax/sdk-ts`，禁裸 fetch `/api/v1`；可选拦截 breaking change：安装 oasdiff 后 `oasdiff breaking <上个提交的 contracts/openapi.json> contracts/openapi.json`（个人项目一期以 git diff 评审为主，不强制装二进制）。

- [ ] **Step 2: 需求文档 §6.5 追加第 8 条**：契约化完成记录（日期、产物路径、验证命令）。

- [ ] **Step 3: 提交（不推送，等口令）**

```bash
git add README.md 产品需求与开发方案.md && git commit -m "stage1 契约化-5: 契约工作流文档"
```

---

## 排期（串行，1 个自然日可完成）

| 时段 | 任务 | 产出验收 |
|---|---|---|
| 上午 ① | Task 1 | /api/v1 + spec 入库 + drift 绿，全量 81 passed |
| 上午 ② | Task 2 | 错误码全声明，82 passed |
| 下午 ① | Task 3 | `pytest -m contract` 绿（预计再 +30~80 用例） |
| 下午 ② | Task 4 + 5 | `cd sdk-ts && npm test` 绿 + 文档；任务 7 解锁 |

风险与预案：schemathesis ASGI 集成 API 漂移（Task 3 Step 2 已内置核对步骤）；fuzz 抓出真 bug 属预期收益，当场修；npm/GitHub 网络问题按项目记忆走 7897 代理或镜像。
