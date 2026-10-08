#!/usr/bin/env python
"""交付自检：一条命令走完"部署完到底能不能用"的全链，逐项给 ✅/❌。

为什么要有它：交付手册里"健康自检"那节是几行 curl，能证明进程活着，**证明不了链路通**——
上传能不能入库、检索能不能召回、问答有没有引用、用量台账记没记、一键重建跑没跑完，
这些只有真跑一遍才知道。而这个脚本跑的就是客户第二天会做的事。

它同时也是**异步档的验收工具**：上传后文档若是 `pending` 就轮询到 ready（同步档直接就是 ready），
重建索引后轮询作业行到终局——同步档靠进程内线程、异步档靠 worker 回报，两条路都走一遍。
（今天修的两个洞正是走这条路才露出来的：镜像少拷 alembic.ini、异步档作业永远收不了尾。）

用法（容器里跑最省事，依赖都在镜像里）：
    docker compose exec backend python scripts/smoke_delivery.py --new-password '新口令8位以上'
    # 或从宿主机打已发布的端口
    python scripts/smoke_delivery.py --base-url http://127.0.0.1:8000 \
        --email admin@umax.local --password '你的口令'

凭据来源：--email/--password 优先，其次环境变量 UMAX_EMAIL/UMAX_PASSWORD，
最后取应用配置里的 ADMIN_EMAIL/ADMIN_PASSWORD（初始化播种的那一对；改过口令就得显式传）。

退出码：0=全绿；1=有硬失败（脚本会给出下一步该怎么修）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402  （在 requirements.txt 里，镜像内可用）

MARK = "退货运费规则-7F3A"      # 唯一标记：让"这份文档"能被确定地检索到
ANSWER_FACT = "退货运费由商家承担"
DOC_NAME = "交付自检-退货规则.txt"

DOC_BODY = f"""售后处理规范（交付自检样本）

一、退货时效
客户签收后 7 个自然日内可申请无理由退货，超期需走人工审核。
生鲜类商品 24 小时内可申请，超时不再受理。

二、退货运费
{MARK}：因商品质量问题发起的退货，{ANSWER_FACT}；因客户主观原因（不喜欢、买错）
发起的退货，运费由客户承担。双方有争议时以平台判定为准。

三、退款到账时间
审核通过后 3 个工作日内原路退回，信用卡渠道可能额外延迟 1-2 个工作日。

四、发票处理
退货需连带退回已开具的发票；发票丢失的订单按实际支付金额的 95% 退款。

五、重复申请
同一订单的退货申请若被驳回，可在 48 小时后重新提交，最多两次。
""" * 3 + f"\n（本文件由交付自检脚本生成，标记 {MARK}）\n"

FAILS: list[str] = []


def title(msg: str) -> None:
    print(f"\n\033[1m{msg}\033[0m" if sys.stdout.isatty() else f"\n{msg}")


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def bad(msg: str, hint: str = "") -> None:
    print(f"  ❌ {msg}")
    if hint:
        print(f"      → {hint}")
    FAILS.append(msg)


def info(msg: str) -> None:
    print(f"     {msg}")


def wait_for(fn, *, timeout: float, interval: float = 2.0, label: str = ""):
    """轮询直到 fn() 返回真值或超时。返回最后一次的值（超时给 None）。"""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    if label:
        info(f"{label} 等待超时（{timeout:.0f}s）")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="交付自检（走完整链路并逐项断言）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--frontend-url", default=None,
                    help="可选：浏览器入口（宿主机 http://127.0.0.1:3000，容器内 http://frontend:3000）。"
                         "给了就额外断言前端代理这条路能打到后端")
    ap.add_argument("--email", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--new-password", default=None,
                    help="首登强改密门闸挡路时用它改密（8 位以上）再继续")
    ap.add_argument("--ready-timeout", type=float, default=180.0, help="等待文档入库的秒数")
    ap.add_argument("--job-timeout", type=float, default=300.0, help="等待重建作业完成的秒数")
    ap.add_argument("--skip-reindex", action="store_true", help="跳过一键重建那一步")
    ap.add_argument("--cleanup", action="store_true", help="结束时删掉自检用的知识库")
    args = ap.parse_args()

    if not args.email or not args.password:
        try:
            from app.core.config import get_settings

            s = get_settings()
            args.email = args.email or s.admin_email
            args.password = args.password or s.admin_password
        except Exception:               # 不在应用环境里跑：只能靠显式传参
            pass
    if not args.email or not args.password:
        print("需要登录凭据：--email/--password（或环境变量 UMAX_EMAIL/UMAX_PASSWORD）")
        return 1

    c = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60.0)
    kb_id = None
    try:
        # ---- 1. 进程活着，且能告诉我们在跑哪份代码 ----
        title("[1/12] 服务与版本")
        r = c.get("/api/v1/health")
        if r.status_code != 200:
            bad(f"/health 返回 {r.status_code}", "后端没起来：docker compose ps / logs backend")
            return 1
        h = r.json()
        ok(f"健康检查 200｜commit={h.get('commit')}｜启动于 {h.get('started_at')}")
        if not h.get("commit") or h["commit"] == "unknown":
            warn("commit 为空/unknown：镜像可能没注入 BUILD_COMMIT，排查'改了不生效'时会缺一手信息")

        # 浏览器走的是**前端代理**这条路（前端把 /api/v1/* rewrite 到后端），和后端直连是两条链。
        # 只打 :8000 会假绿——真机踩过：前端容器里 rewrite 打的是 127.0.0.1:8000（它自己），
        # 登录页拉 /auth/me、/branding 全 ECONNREFUSED 报 Internal Server Error，而后端直连 12/12 全绿。
        if args.frontend_url:
            fc = httpx.Client(base_url=args.frontend_url.rstrip("/"), timeout=30.0)
            try:
                fr = fc.get("/api/v1/branding")      # 经代理的 API（登录页首屏就靠它）
                lp = fc.get("/admin/login")          # 登录页本体
                if fr.status_code == 200 and lp.status_code == 200:
                    ok(f"前端代理通：{args.frontend_url}｜登录页 {lp.status_code}｜"
                       f"经代理的 /api/v1/branding {fr.status_code}")
                else:
                    bad(f"前端 {args.frontend_url} 不通：登录页 {lp.status_code}、"
                        f"经代理的 /api/v1/branding {fr.status_code}（浏览器看到的就是这条路）",
                        "rewrite 的目标在**构建期**烧进产物：容器里必须是 http://backend:8000。"
                        "改完要 docker compose up -d --build frontend 重建，光重启容器不生效")
            except Exception as e:                   # 连不上/超时：前端没起或端口没发布
                bad(f"前端 {args.frontend_url} 连不上：{e}",
                    "确认容器在跑、端口已发布；容器内跑本脚本时用 --frontend-url http://frontend:3000")
            finally:
                fc.close()
        else:
            info("未传 --frontend-url：本次不覆盖浏览器那条路（前端代理 → 后端）——"
                 "从宿主机跑时建议带 --frontend-url http://127.0.0.1:3000")

        # ---- 2. 登录（含首登强改密门闸）----
        title("[2/12] 登录与会话")
        r = c.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        if r.status_code == 429:
            bad("登录被限流（429）", "等 15 分钟，或重启后端重置节流计数")
            return 1
        if r.status_code != 204:
            bad(f"登录失败：{r.status_code} {r.text[:120]}",
                "邮箱/口令不对，或账号被停用。忘了口令就用 --password 传对的；"
                "全新部署用 ADMIN_EMAIL/ADMIN_PASSWORD 那一对")
            return 1
        me = c.get("/api/v1/auth/me")
        if me.status_code != 200:
            bad(f"/auth/me 返回 {me.status_code} {me.text[:120]}",
                "刚登录就 401：会话 cookie 没存住（反代把 Set-Cookie 吃掉了？）")
            return 1
        me_j = me.json()
        # 首登强改密门闸：`/auth/me` 是豁免端点（前端要靠它知道该弹改密框），
        # 所以**它返回 200 + must_change_password=true，而不是 428**——此后所有受护端点才是 428。
        # （这条是实跑才发现的：我原先按 428 判定，于是门闸没被识别、卡在后面某一步报 428。）
        if me_j.get("must_change_password"):
            if not args.new_password:
                bad("账号卡在「必须修改初始口令」门禁上：未改密前受护端点一律 428",
                    "先完成初始化向导（浏览器里改密），或加 --new-password '新口令' 再跑本脚本")
                return 1
            r = c.post("/api/v1/auth/change-password",
                       json={"old_password": args.password, "new_password": args.new_password})
            if r.status_code != 204:
                bad(f"首登改密失败：{r.status_code} {r.text[:120]}", "新口令要 8 位以上")
                return 1
            args.password = args.new_password
            me_j = c.get("/api/v1/auth/me").json()
            ok("已通过首登强改密门禁（口令已更新——后续请用新口令）")
        ok(f"会话有效：{me_j['email']}（role={me_j['role']}）")
        if me_j["role"] != "admin":
            bad("自检需要 admin 账号（建库/上传/重建都是 admin 面）", "换用 admin 账号")
            return 1

        # ---- 3. 匿名面（登录页首屏要靠它）----
        title("[3/12] 匿名面与白标")
        r = c.get("/api/v1/branding")
        if r.status_code == 200:
            ok(f"白标可读：品牌名「{r.json().get('brand_name')}」")
        else:
            bad(f"/branding 返回 {r.status_code}", "登录页要显品牌，这个端点必须匿名可读")

        # ---- 4. 授权门闸（enforced 且无效时写操作全 403，后面都别做了）----
        title("[4/12] 授权状态")
        lic = c.get("/api/v1/license").json()
        if lic.get("enforced"):
            if lic.get("valid"):
                ok(f"授权有效：{lic.get('customer')}｜剩余 {lic.get('days_left')} 天")
            else:
                bad(f"授权不可用：{lic.get('reason')}",
                    "写操作全被拦（403），先按 docs/DEPLOY.md 第 4 步放授权文件")
                return 1
        else:
            warn("未启用授权校验（开发模式）——生产交付必须配 LICENSE_PUBLIC_KEY")

        # ---- 5. 模型通路 ----
        title("[5/12] 模型通路")
        mr = c.get("/api/v1/models")
        if mr.status_code != 200:
            bad(f"/models 返回 {mr.status_code} {mr.text[:120]}",
                "503=未配 GATEWAY_SECRET（模型 key 没法加密存储）")
            return 1
        models = mr.json()
        by_scenario: dict[str, int] = {}
        for m in models:
            if m.get("enabled"):
                by_scenario[m["scenario"]] = by_scenario.get(m["scenario"], 0) + 1
        info(f"后台登记的启用模型：{by_scenario or '（无）'}")
        if not by_scenario.get("chat"):
            warn("没有启用的 chat 模型：问答会退到 .env 直连（DASHSCOPE_API_KEY）；"
                 "两侧都没有就会一直走「未命中兜底」——第 10 步会如实暴露")
        if not by_scenario.get("embedding"):
            warn("没有启用的 embedding 模型：入库会退化成「只建 BM25、不存向量」"
                 "（第 8 步会看到 has_embedding 全 false）")

        # ---- 6~8. 建库 → 上传 → 入库 → 切块/向量 ----
        title("[6/12] 建库与上传")
        kb = c.post("/api/v1/kb", json={"name": f"交付自检-{int(time.time())}",
                                       "description": "smoke_delivery.py 生成，可删"})
        if kb.status_code != 201:
            bad(f"建库失败：{kb.status_code} {kb.text[:160]}")
            return 1
        kb_id = kb.json()["id"]
        ok(f"已建知识库 #{kb_id}")

        up = c.post(f"/api/v1/kb/{kb_id}/documents",
                    files={"file": (DOC_NAME, DOC_BODY.encode("utf-8"), "text/plain")})
        if up.status_code != 201:
            bad(f"上传失败：{up.status_code} {up.text[:160]}",
                "415=不支持的类型；403=授权/权限；404=库不存在")
            return 1
        doc = up.json()
        doc_id = doc["id"]
        ok(f"已上传《{DOC_NAME}》（{len(DOC_BODY.encode('utf-8')) // 1024} KB，初始状态 {doc['status']}）")

        title("[7/12] 文档入库")
        if doc["status"] == "pending":
            info("状态 pending → 异步入库档，轮询等 worker 接手…")

        def _doc_state():
            j = c.get(f"/api/v1/documents/{doc_id}").json()
            return j if j["status"] in ("ready", "failed") else None

        st = _doc_state() if doc["status"] in ("ready", "failed") else wait_for(
            _doc_state, timeout=args.ready_timeout, label="入库")
        if st is None:
            bad(f"入库超时（{args.ready_timeout:.0f}s 仍未 ready）",
                "异步档看 worker 日志：docker compose logs worker（有没有连上 redis？）")
            return 1
        if st["status"] != "ready":
            bad(f"入库失败：{st.get('error')}", "按错误文案处理（原文件不可读/解析器不支持等）")
            return 1
        ok("入库完成（status=ready）")

        title("[8/12] 切块与向量")
        chunks = c.get(f"/api/v1/documents/{doc_id}/chunks").json()
        if not chunks:
            bad("切块为空：检索永远命不中", "看文档解析是否把内容读没了")
            return 1
        emb = sum(1 for ch in chunks if ch["has_embedding"])
        ok(f"切块 {len(chunks)} 块，其中 {emb} 块带向量")
        if emb == 0:
            warn("全部没有向量 → 当前是「只建 BM25」降级态（缺少 embedding 模型/调用失败）；"
                 "语义检索不可用，同义不同词的问题会答不上")

        # ---- 9. 检索 ----
        title("[9/12] 混合检索")
        hits = c.post("/api/v1/retrieve",
                      json={"query": f"{MARK} 怎么规定的", "kb_ids": [kb_id], "top_k": 5}).json()
        if not hits:
            bad("检索 0 命中：问答必然走未命中兜底", "看上面切块内容是否含标记词")
            return 1
        ok(f"召回 {len(hits)} 条，首条来自《{hits[0]['doc_name']}》"
           f"（bm25={hits[0]['bm25_hit']} vec={hits[0]['vec_hit']}）")

        # ---- 10. 问答 + 引用 ----
        title("[10/12] 带引用问答")
        miss = None
        try:
            miss = c.get("/api/v1/settings").json()["values"]["chat_miss_answer"]
        except Exception:
            pass
        ans = c.post("/api/v1/chat",
                     json={"question": f"{MARK} 的退货运费由谁承担？", "kb_ids": [kb_id]})
        if ans.status_code != 200:
            bad(f"问答失败：{ans.status_code} {ans.text[:200]}",
                "429=配额用尽；404=会话不存在")
            return 1
        a = ans.json()
        info(f"回答（前 120 字）：{a['answer'][:120].replace(chr(10), ' ')}")
        info(f"引用 {len(a['citations'])} 条，命中文档 {a['cited_docs']}")
        if miss and a["answer"].strip() == miss.strip():
            bad("问答走了「未命中兜底」话术：说明模型没被调用（未配 chat 模型且无直连 key）",
                "到「模型」页登记 chat 模型，或在 .env 配 DASHSCOPE_API_KEY")
            return 1
        if not a["citations"]:
            bad("回答没有引用：溯源能力没生效", "看检索是否命中（上一步）")
            return 1
        if DOC_NAME not in a["cited_docs"]:
            warn(f"引用里没有本自检文档（{a['cited_docs']}）——可能是模型选择引用别的片段")
        ok(f"问答成功，conversation_id={a['conversation_id']}")

        # ---- 11. 用量台账 + 会话历史 ----
        title("[11/12] 用量台账与会话")
        q = c.get("/api/v1/usage/me").json()
        if q["daily_used"] > 0:
            ok(f"台账已记账：今日 {q['daily_used']} tokens"
               + (f"（上限 {q['daily_limit']}）" if q["daily_limit"] else ""))
        else:
            bad("用量台账为 0：这次问答没有记账", "成本闸门会失效，查 chat 是否真的调了模型")
        msgs = c.get(f"/api/v1/conversations/{a['conversation_id']}/messages").json()
        if len(msgs) >= 2:
            ok(f"会话历史完整（{len(msgs)} 条消息：user + assistant）")
        else:
            bad(f"会话消息只有 {len(msgs)} 条", "历史落库有问题")

        # ---- 12. 一键重建（同步档=进程内线程；异步档=worker 回报，两条路都验）----
        title("[12/12] 一键重建索引与后台作业")
        if args.skip_reindex:
            info("已按 --skip-reindex 跳过")
        else:
            ri = c.post("/api/v1/reindex", json={"kb_ids": [kb_id]})
            if ri.status_code != 202:
                bad(f"重建请求失败：{ri.status_code} {ri.text[:160]}")
                return 1
            job_id = ri.json()["job_id"]
            info(f"已发起重建 job #{job_id}（{ri.json()['documents']} 篇）")

            def _job():
                rows = c.get("/api/v1/jobs", params={"limit": 5}).json()
                rows = rows if isinstance(rows, list) else []
                return next((j for j in rows if j["id"] == job_id
                             and j["status"] in ("done", "failed", "interrupted")), None)

            job = wait_for(_job, timeout=args.job_timeout, label=f"作业 #{job_id}")
            if job is None:
                bad(f"重建作业超时未收尾（{args.job_timeout:.0f}s）",
                    "异步档重点看 worker 日志与 redis；作业行由 worker 每篇回报一次")
                return 1
            ok(f"作业收尾：status={job['status']}｜{job['done']}/{job['total']} 篇成功"
               f"｜失败 {job['failed']}")
            if job["status"] != "done" or job["failed"]:
                warn(f"有失败篇目：{job.get('error') or '看文档列表的失败原因'}")
            back = c.get(f"/api/v1/documents/{doc_id}").json()
            if back["status"] == "ready":
                ok("重建后文档回到 ready")
            else:
                bad(f"重建后文档状态是 {back['status']}：{back.get('error')}")

        # ---- 可选清理 ----
        if args.cleanup and kb_id:
            r = c.delete(f"/api/v1/kb/{kb_id}")
            ok(f"已清理自检知识库 #{kb_id}（{r.status_code}）")
        elif kb_id:
            info(f"自检知识库 #{kb_id} 保留（可到界面里看效果；删除用 --cleanup 或界面上删）")

    finally:
        c.close()

    print()
    if FAILS:
        print(f"❌ 交付自检未通过：{len(FAILS)} 项失败")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 交付自检全部通过（12 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
