"use client";
// 评测（/admin/eval，admin 面）：金标准集管理 + 跑分 + 历史 + 逐题明细 + 报告导出。
// 这一页是"凭什么说更准"的答案：改完检索参数点一下开始评测，用数字说话、和上一轮逐项对比。
// 分数刻意分三处看（通过率 / 检索命中率 / MRR）：检索没命中而答案对了是真实存在的（答案来自别的块），
// 混成一个数就无法归因到底是检索该调还是生成该调。
// 为什么进产品页而不留个脚本：脚本给不了"跑完就能看、历史都在、数字可比"。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync, usePolling } from "@/lib/hooks";
import type { EvalQuestionOut, EvalRunDetailOut, EvalRunOut } from "@/lib/types";

const pct = (x: number) => `${Math.round((x || 0) * 100)}%`;
const fmtTime = (s: string | null) => (s ? s.slice(0, 16).replace("T", " ") : "—");
// retrieval 可能是 null（该题没设金标准文档=不适用）：只有 true 才算过，其余一律 ❌
const mark = (v: boolean | null | undefined) => (v === true ? "✅" : "❌");
// 期望词与文档名用逗号/顿号/空白分隔——手输一个词表比玩 JSON 数组现实得多
const split = (s: string) => s.split(/[,，、\s]+/).map((x) => x.trim()).filter(Boolean);
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const EMPTY_DRAFT = { question: "", expect_all: "", expect_any: "", cites: "",
                      category: "", note: "" };

export default function EvalAdmin({ api }: { api: Client }) {
  const [tab, setTab] = useState<"runs" | "questions">("runs");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [editing, setEditing] = useState<number | null>(null);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);

  const questions = useAsync(() => call(api.GET(P.evalQuestions)) as Promise<EvalQuestionOut[]>);
  // 有 running 的连跑就轮询（1.5s）；全终态自然停。——usePolling 的 deps=[tick,enabled] 不感知 fn，
  // 所以"起一轮新评测/点另一条历史"都必须显式 reload() 换轮询目标（同 KbAdmin 切库的裁决）
  const runs = usePolling(
    () => call(api.GET(P.evalRuns)) as Promise<EvalRunOut[]>,
    { intervalMs: 1500, stopWhen: (rs) => !rs.some((r) => r.status === "running"),
      enabled: true });
  const detail = usePolling(
    () => {
      if (activeRunId === null) return Promise.resolve(null);
      const p = call(api.GET(P.evalRun, { params: { path: { run_id: activeRunId } } }));
      return p as Promise<EvalRunDetailOut>;
    },
    { intervalMs: 1500, stopWhen: (d) => !d || d.status !== "running",
      enabled: activeRunId !== null });

  async function startRun() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    setReport(null);
    try {
      const run = await call(api.POST(P.evalRuns, { body: {} }) as never) as EvalRunOut;
      setActiveRunId(run.id);
      runs.reload();
      detail.reload();
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(false);
    }
  }

  function openRun(id: number) {
    setActiveRunId(id);
    setReport(null);
    detail.reload();
  }

  async function loadReport(id: number) {
    setErr(null);
    try {
      const out = await call(api.GET(P.evalRunReport,
        { params: { path: { run_id: id } } })) as { markdown: string };
      setReport(out.markdown);
    } catch (e) {
      setErr(msg(e));
    }
  }

  function downloadReport(id: number) {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([report], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `评测报告-${id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function delRun(id: number) {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await callVoid(api.DELETE(P.evalRun, { params: { path: { run_id: id } } }) as never);
      if (activeRunId === id) { setActiveRunId(null); setReport(null); }
      runs.reload();
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveQuestion() {
    if (busy) return;
    const shared = {
      question: draft.question.trim(),
      expect_all: split(draft.expect_all),
      expect_any: split(draft.expect_any),
      cites: split(draft.cites),
      category: draft.category.trim(),
      note: draft.note.trim() || null,
    };
    if (!shared.question) { setErr("问题不能为空"); return; }
    setBusy(true);
    setErr(null);
    try {
      if (editing !== null) {
        await callVoid(api.PATCH(P.evalQuestion,
          { params: { path: { qid: editing } }, body: shared }) as never);
      } else {
        // enabled 显式带上：契约里它带默认值（defaultNonNullable），前端就该把创建意图说全
        await callVoid(api.POST(P.evalQuestions, { body: { ...shared, enabled: true } }) as never);
      }
      setDraft(EMPTY_DRAFT);
      setEditing(null);
      questions.reload();
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleQuestion(q: EvalQuestionOut) {
    setErr(null);
    try {
      await callVoid(api.PATCH(P.evalQuestion,
        { params: { path: { qid: q.id } }, body: { enabled: !q.enabled } }) as never);
      questions.reload();
    } catch (e) {
      setErr(msg(e));
    }
  }

  async function delQuestion(id: number) {
    setErr(null);
    try {
      await callVoid(api.DELETE(P.evalQuestion, { params: { path: { qid: id } } }) as never);
      setConfirmDel(null);
      questions.reload();
    } catch (e) {
      setErr(msg(e));
    }
  }

  const list = runs.data ?? [];
  const d = detail.data;
  const active = activeRunId === null ? null : list.find((r) => r.id === activeRunId) ?? d;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={runs.error ?? questions.error ?? err} />
      <div className="flex items-center gap-2">
        {([["runs", "评测结果"], ["questions", "金标准集"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
                  className={`rounded-lg px-3 py-1.5 text-body transition-colors ${
                    tab === k ? "bg-brand-soft font-medium text-ink-1" : "text-muted-foreground hover:bg-accent"}`}>
            {label}
            {k === "questions" && questions.data ? `（${questions.data.length}）` : ""}
          </button>
        ))}
      </div>

      {tab === "runs" && (
        <>
          <div className="space-y-3 rounded-xl border border-border bg-card px-6 py-4 shadow-sm">
            <div className="flex items-center gap-3">
              <Button className="h-9 rounded-lg" disabled={busy} onClick={() => void startRun()}>
                {busy ? "提交中…" : "开始评测"}
              </Button>
              <span className="text-caption text-ink-3">
                对全部启用中的金标准题跑一遍完整问答链路（检索 → 生成 → 引用），
                在全部知识库范围内评测；预算 20 题一轮约 1~2 分钟。
              </span>
            </div>
            <p className="text-caption text-ink-3">
              改完检索参数（召回条数/最低相似度/提示词）跑一轮，与本页历史逐项对比，就知道有没有变准。
            </p>
          </div>

          {active && (
            <div className="space-y-4 rounded-xl border border-border bg-card px-6 py-4 shadow-sm">
              <div className="flex items-center gap-3">
                <h2 className="text-h2 font-semibold">#{active.id} 评测结果</h2>
                <span className="text-body text-ink-3">
                  {active.status === "running"
                    ? `进行中 ${active.total} 题…`
                    : active.status === "failed" ? "执行失败" : `${active.passed}/${active.total} 通过`}
                </span>
                {active.status === "done" && (
                  <>
                    <Button size="sm" variant="ghost" className="ml-auto h-7 rounded-lg text-ink-3"
                            onClick={() => void loadReport(active.id)}>报告</Button>
                    {report && (
                      <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-3"
                              onClick={() => downloadReport(active.id)}>下载</Button>
                    )}
                  </>
                )}
              </div>
              {active.error && <p className="text-body text-destructive">{active.error}</p>}
              <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
                {[["通过率", pct(active.metrics.pass_rate)],
                  ["检索命中率", `${active.metrics.hit}/${active.metrics.with_cites}`],
                  ["MRR", active.metrics.mrr.toFixed(3)],
                  ["平均耗时", `${active.metrics.avg_latency_ms} ms`]].map(([k, v]) => (
                  <div key={k}>
                    <div className="text-caption text-ink-3">{k}</div>
                    <div className="text-h3 font-medium text-ink-1">{v}</div>
                  </div>
                ))}
              </div>
              {active.metrics.categories.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-body">
                    <thead><tr className="text-left text-caption text-ink-3">
                      <th className="py-1 pr-4 font-medium">考察点</th>
                      <th className="py-1 pr-4 font-medium">通过</th></tr></thead>
                    <tbody>
                      {active.metrics.categories.map((c) => (
                        <tr key={c.category} className="border-t border-border/60">
                          <td className="py-1.5 pr-4 text-ink-2">{c.category}</td>
                          <td className="py-1.5 pr-4 text-ink-2">{c.passed}/{c.total}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {(d?.items ?? []).length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-body">
                    <thead><tr className="text-left text-caption text-ink-3">
                      <th className="py-1 pr-4 font-medium">#</th>
                      <th className="py-1 pr-4 font-medium">问题</th>
                      <th className="py-1 pr-4 font-medium">考察点</th>
                      <th className="py-1 pr-4 font-medium">判据</th>
                      <th className="py-1 pr-4 font-medium">金标准位次</th>
                      <th className="py-1 pr-4 font-medium">耗时</th></tr></thead>
                    <tbody>
                      {(d?.items ?? []).map((it, i) => (
                        <tr key={it.id} className="border-t border-border/60 align-top">
                          <td className="py-2 pr-4 text-ink-3">{i + 1}</td>
                          <td className="py-2 pr-4">
                            <details>
                              <summary className="cursor-pointer text-ink-1">
                                {it.passed ? "✅" : "❌"} {it.question}
                              </summary>
                              <div className="mt-2 space-y-1 text-caption text-ink-2">
                                <p>期望词：{it.expect_all.join("、") || "—"}
                                  {it.expect_any.length > 0 ? `（任一：${it.expect_any.join("、")}）` : ""}</p>
                                <p>命中Top5：{it.top_docs.join("、") || "无"}</p>
                                <p>引用：{it.cited_docs.join("、") || "无"}</p>
                                <pre className="whitespace-pre-wrap text-body text-ink-1">{it.answer ?? "（无回答）"}</pre>
                                {it.error && <p className="text-destructive">错误：{it.error}</p>}
                              </div>
                            </details>
                          </td>
                          <td className="py-2 pr-4 text-ink-3">{it.category || "—"}</td>
                          <td className="py-2 pr-4 text-ink-2">
                            词{mark(it.checks.kw_all)}
                            {it.expect_any.length > 0 && ` 任一${mark(it.checks.kw_any)}`}
                            引用{mark(it.checks.citation)}
                            {it.cites.length > 0 && ` 检索${mark(it.checks.retrieval)}`}
                          </td>
                          <td className="py-2 pr-4 text-ink-2">{it.cites.length === 0 ? "—" : (it.rank || "未命中")}</td>
                          <td className="py-2 pr-4 text-ink-3">{it.latency_ms ?? "—"} ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {report && (
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/40 px-4 py-3 text-body text-ink-1">
                  {report}
                </pre>
              )}
            </div>
          )}

          <div className="space-y-2">
            <h3 className="text-h3 font-medium text-ink-2">历史</h3>
            {list.length === 0 && (
              <p className="rounded-xl border border-border bg-card px-6 py-4 text-body text-ink-3">
                还没有评测记录。点上面的「开始评测」跑第一轮——它会成为后续所有「变准了没有」的基准。
              </p>
            )}
            {list.length > 0 && (
              <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
                <table className="w-full text-body">
                  <thead><tr className="text-left text-caption text-ink-3">
                    <th className="px-4 py-2 font-medium">#</th>
                    <th className="px-4 py-2 font-medium">时间</th>
                    <th className="px-4 py-2 font-medium">状态</th>
                    <th className="px-4 py-2 font-medium">通过</th>
                    <th className="px-4 py-2 font-medium">检索命中率</th>
                    <th className="px-4 py-2 font-medium">MRR</th>
                    <th className="px-4 py-2 font-medium">操作</th></tr></thead>
                  <tbody>
                    {list.map((r) => (
                      <tr key={r.id} className="border-t border-border/60">
                        <td className="px-4 py-2 text-ink-3">{r.id}</td>
                        <td className="px-4 py-2 text-ink-2">{fmtTime(r.started_at)}</td>
                        <td className="px-4 py-2 text-ink-2">
                          {r.status === "running" ? `进行中 ${r.total}` :
                            r.status === "failed" ? "失败" : "已完成"}
                        </td>
                        <td className="px-4 py-2 text-ink-1">{r.passed}/{r.total}（{pct(r.metrics.pass_rate)}）</td>
                        <td className="px-4 py-2 text-ink-2">
                          {r.metrics.hit}/{r.metrics.with_cites}（{pct(r.metrics.hit_rate)}）
                        </td>
                        <td className="px-4 py-2 text-ink-2">{r.metrics.mrr.toFixed(3)}</td>
                        <td className="px-4 py-2">
                          <span className="inline-flex items-center gap-1 whitespace-nowrap">
                            <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-3"
                                    onClick={() => openRun(r.id)}>查看</Button>
                            <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive"
                                    disabled={busy} onClick={() => void delRun(r.id)}>删除</Button>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {tab === "questions" && (
        <>
          <div className="space-y-3 rounded-xl border border-border bg-card px-6 py-4 shadow-sm">
            <h3 className="text-h3 font-medium text-ink-2">
              {editing === null ? "新增金标准题" : `编辑第 ${editing} 题`}
            </h3>
            <p className="text-caption text-ink-3">
              期望词是判分依据：必须出现的写「必须含词」，出现其一即可的写「任一含词」；
              期望文档名用于算检索命中率与 MRR（填资料里的文件名，如 rag_dirty_doc_01.txt）。
            </p>
            <div className="space-y-2">
              <Input aria-label="问题" placeholder="问题，如：生鲜能七天无理由退货吗" className="h-9 rounded-lg border-border"
                     value={draft.question} onChange={(e) => setDraft({ ...draft, question: e.target.value })} />
              <Input aria-label="必须含词" placeholder="必须含词（逗号分隔），如：不支持" className="h-9 rounded-lg border-border"
                     value={draft.expect_all} onChange={(e) => setDraft({ ...draft, expect_all: e.target.value })} />
              <Input aria-label="任一含词" placeholder="任一含词（逗号分隔），如：坏果, 按比例赔偿" className="h-9 rounded-lg border-border"
                     value={draft.expect_any} onChange={(e) => setDraft({ ...draft, expect_any: e.target.value })} />
              <Input aria-label="期望文档" placeholder="期望文档名（逗号分隔），如：rag_dirty_doc_01.txt" className="h-9 rounded-lg border-border"
                     value={draft.cites} onChange={(e) => setDraft({ ...draft, cites: e.target.value })} />
              <Input aria-label="考察点" placeholder="考察点，如：错别字干扰" className="h-9 rounded-lg border-border"
                     value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
              <Input aria-label="备注" placeholder="备注：这题为什么这么判（校准记录，长期很有用）" className="h-9 rounded-lg border-border"
                     value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} />
            </div>
            <div className="flex items-center gap-3">
              <Button className="h-9 rounded-lg" disabled={busy} onClick={() => void saveQuestion()}>
                {editing === null ? "添加" : "保存"}
              </Button>
              {editing !== null && (
                <Button variant="ghost" className="h-9 rounded-lg text-ink-3"
                        onClick={() => { setEditing(null); setDraft(EMPTY_DRAFT); }}>取消</Button>
              )}
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
            <table className="w-full text-body">
              <thead><tr className="text-left text-caption text-ink-3">
                <th className="px-4 py-2 font-medium">#</th>
                <th className="px-4 py-2 font-medium">问题</th>
                <th className="px-4 py-2 font-medium">考察点</th>
                <th className="px-4 py-2 font-medium">必须含词</th>
                <th className="px-4 py-2 font-medium">期望文档</th>
                <th className="px-4 py-2 font-medium">状态</th>
                <th className="px-4 py-2 font-medium">操作</th></tr></thead>
              <tbody>
                {(questions.data ?? []).map((q) => (
                  <tr key={q.id} className="border-t border-border/60">
                    <td className="px-4 py-2 text-ink-3">{q.id}</td>
                    <td className="px-4 py-2">
                      <span className="block max-w-72 truncate text-ink-1" title={q.question}>{q.question}</span>
                    </td>
                    <td className="px-4 py-2 text-ink-3">{q.category || "—"}</td>
                    <td className="px-4 py-2 text-ink-2">{q.expect_all.join("、") || "—"}</td>
                    <td className="px-4 py-2 text-ink-2">{q.cites.join("、") || "—"}</td>
                    <td className="px-4 py-2 text-ink-3">{q.enabled ? "启用" : "停用"}</td>
                    <td className="px-4 py-2">
                      <span className="inline-flex items-center gap-1 whitespace-nowrap">
                        <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-3"
                                onClick={() => { setEditing(q.id); setDraft({
                                  question: q.question, expect_all: q.expect_all.join(", "),
                                  expect_any: q.expect_any.join(", "), cites: q.cites.join(", "),
                                  category: q.category, note: q.note ?? "" }); }}>编辑</Button>
                        <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-3"
                                onClick={() => void toggleQuestion(q)}>{q.enabled ? "停用" : "启用"}</Button>
                        {confirmDel === q.id ? (
                          <>
                            <Button size="sm" className="h-7 rounded-lg bg-destructive text-card hover:bg-destructive/90"
                                    onClick={() => void delQuestion(q.id)}>确认删除</Button>
                            <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-3"
                                    onClick={() => setConfirmDel(null)}>取消</Button>
                          </>
                        ) : (
                          <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive"
                                  onClick={() => setConfirmDel(q.id)}>删除</Button>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
