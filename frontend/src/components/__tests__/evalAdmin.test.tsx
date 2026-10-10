// 评测页（/admin/eval）：分数与分类上屏、起评测会 POST 并刷新、running 轮询到终态即停、
// 明细判据可归因、报告可拉取、金标准集增改停删（含两步删除）。
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import EvalAdmin from "@/components/EvalAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { DocOut, EvalQuestionOut, EvalRunDetailOut, EvalRunOut } from "@/lib/types";

const question: EvalQuestionOut = {
  id: 1, question: "生鲜能七天无理由退货吗", expect_all: ["不支持"], expect_any: [],
  cites: ["rag_dirty_doc_01.txt"], category: "错别字干扰", note: "校准记录",
  enabled: true, created_at: "2026-10-08T09:00:00+00:00" };

const NO_JUDGE = { judged: 0, scored: 0, faithful: 0, relevance: 0, faithful_rate: 0,
                   relevance_rate: 0, model: null };
const metrics = { total: 20, passed: 18, pass_rate: 0.9, with_cites: 20, hit: 20,
  hit_rate: 1.0, mrr: 0.975, avg_latency_ms: 1200,
  categories: [{ category: "版本冲突", total: 3, passed: 3 }], judge: NO_JUDGE };

const run = (over: Partial<EvalRunOut> = {}): EvalRunOut => ({
  id: 1, status: "done", total: 20, passed: 18, metrics, kb_ids: null, judge: false,
  chat_model: "qwen3.7-flash", embedding_model: "qwen3.7-text-embedding", error: null,
  created_by: "admin@umax.local", started_at: "2026-10-08T10:00:00+00:00",
  finished_at: "2026-10-08T10:01:00+00:00", ...over });

const detail = (over: Partial<EvalRunDetailOut> = {}): EvalRunDetailOut => ({
  ...run(), items: [
    { id: 1, question_id: 1, question: "生鲜能七天无理由退货吗", category: "错别字干扰",
      note: null, expect_all: ["不支持"], expect_any: [], cites: ["rag_dirty_doc_01.txt"],
      answer: "生鲜不支持七天无理由 [1]", cited_docs: ["rag_dirty_doc_01.txt"],
      top_docs: ["rag_dirty_doc_01.txt"], checks: { kw_all: true, kw_any: true, citation: true,
        retrieval: true, passed: true, rank: 1 }, judge: null, passed: true, rank: 1,
      latency_ms: 800, error: null },
    { id: 2, question_id: 2, question: "运费谁承担", category: "缺失内容", note: null,
      expect_all: ["商家承担"], expect_any: [], cites: ["rag_dirty_doc_01.txt"],
      answer: "资料里没有相关内容，无法回答。", cited_docs: [], top_docs: ["rag_dirty_doc_01.txt"],
      checks: { kw_all: false, kw_any: true, citation: false, retrieval: true, passed: false,
        rank: 1 },
      judge: null,
      passed: false, rank: 1, latency_ms: 700, error: null }], ...over });

const renderPage = (stubs: Parameters<typeof fakeApi>[0]) =>
  render(<EvalAdmin api={fakeApi({
    ...stubs,
    // 金标准集在挂载时就拉（与 tab 无关），所有用例统一兜住
    GET: async (url: string, init?: unknown) => {
      if (url === P.evalQuestions) return ok([question]);
      const own = stubs.GET ? stubs.GET(url, init) : undefined;
      if (own !== undefined) return own;         // 用例自己的存根优先
      // 评测前置自检会查"库里有没有金标准文档"：默认给一份"存在"，缺文档的用例自行覆盖
      if (url === P.kb) return ok([{ id: 1, name: "评测库", description: null }]);
      if (url === P.kbDocs) return ok([docNamed(question.cites[0])]);
      return undefined;
    },
  })} />);

const docNamed = (name: string): DocOut => ({ id: 1, kb_id: 1, name, status: "ready",
  error: null, size_bytes: 10, created_at: "2026-10-07T10:00:00+00:00", has_embedding: true });

describe("评测页", () => {
  it("历史与分数上屏；未改动时是空态提示", async () => {
    renderPage({ GET: (url) => (url === P.evalRuns ? ok([run()]) : undefined) });
    expect(await screen.findByText("18/20（90%）")).toBeInTheDocument();
    expect(screen.getByText("20/20（100%）")).toBeInTheDocument();   // 检索命中率单独一列
    expect(screen.getByText("0.975")).toBeInTheDocument();           // MRR
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("空历史给的是「先跑一轮」的引导，而不是空白表格", async () => {
    renderPage({ GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    expect(await screen.findByText(/点上面的「开始评测」跑第一轮/)).toBeInTheDocument();
  });

  it("库里没有金标准文档时先提示，避免白跑一轮（语料不配套会全 0 且看不出原因）", async () => {
    const POST = vi.fn(async () => ok(run()));
    renderPage({
      POST,
      GET: (url: string) => (url === P.evalRuns ? ok([])
        : url === P.kbDocs ? ok([docNamed("无关文档.txt")]) : undefined),
    });
    await userEvent.click(await screen.findByRole("button", { name: "开始评测" }));
    const dialog = await screen.findByRole("dialog", { name: "评测前提示" });
    expect(within(dialog).getByText("rag_dirty_doc_01.txt")).toBeInTheDocument();
    expect(POST).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "仍然评测" }));
    await waitFor(() => expect(POST).toHaveBeenCalled());
  });

  it("点开始评测：POST 空体并刷新历史（新记录立刻出现）", async () => {
    let created = false;
    const second = run({ id: 2, passed: 7, total: 20,
                         metrics: { ...metrics, passed: 7, pass_rate: 0.35 } });
    const POST = vi.fn(async () => { created = true; return ok(second); });
    const GET = vi.fn(async (url: string) => (url === P.evalRuns
      ? ok(created ? [second, run()] : [run()]) : ok(detail())));
    renderPage({ POST, GET });
    await userEvent.click(await screen.findByRole("button", { name: "开始评测" }));
    await waitFor(() => expect(POST).toHaveBeenCalledWith(P.evalRuns, { body: { judge: false } }));
    // 起完立即 reload 历史：新记录出现（不 reload 的话 usePolling 不感知 fn，列表会一直是旧的）
    expect(await screen.findByText(/7\/20（35%）/)).toBeInTheDocument();
  });

  it("勾上裁判再起评测：body 带 judge=true，结果里裁判分单独一段", async () => {
    const judged = run({
      id: 3, judge: true,
      metrics: { ...metrics, judge: { judged: 20, scored: 20, faithful: 18, relevance: 20,
                                      faithful_rate: 0.9, relevance_rate: 1.0,
                                      model: "qwen3.7-flash" } } });
    const POST = vi.fn(async () => ok(judged));
    const GET = vi.fn(async (url: string) => (url === P.evalRuns ? ok([judged]) : ok(detail({
      judge: true,
      metrics: judged.metrics,
      items: [{ ...detail().items[0], judge: { faithful: 1, relevance: 0,
                                               reason: "没回答问题", raw: null,
                                               model: "qwen3.7-flash" } }] }))));
    renderPage({ POST, GET });
    await userEvent.click(await screen.findByLabelText("请裁判模型评分"));
    await userEvent.click(await screen.findByRole("button", { name: "开始评测" }));
    await waitFor(() => expect(POST).toHaveBeenCalledWith(P.evalRuns, { body: { judge: true } }));
    // 裁判分与通过率分栏呈现（不是混成一个总分），且写明"不并入通过率"
    expect(await screen.findByText(/裁判评分（qwen3.7-flash，不并入通过率）/)).toBeInTheDocument();
    expect(screen.getByText(/faithfulness 18\/20（90%）/)).toBeInTheDocument();
    expect(screen.getByText("已完成·含裁判")).toBeInTheDocument();
    // 逐题能看到裁判判词与理由（"为什么扣分"要落到题上才有用）
    await userEvent.click(screen.getByText(/✅ 生鲜能七天无理由退货吗/));
    expect(await screen.findByText(/裁判：faithfulness=1 relevance=0——没回答问题/)).toBeInTheDocument();
  });

  it("上一轮失败的原因直接上屏（不让用户猜）", async () => {
    const POST = vi.fn(async () => fail("没有启用中的金标准题：先到金标准集里添加或启用", 400));
    renderPage({ POST, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(await screen.findByRole("button", { name: "开始评测" }));
    expect(await screen.findByText(/没有启用中的金标准题/)).toBeInTheDocument();
  });

  it("查看：逐题判据可归因（检索过但答案不达标要看得出来）", async () => {
    renderPage({
      GET: (url) => (url === P.evalRuns ? ok([run()]) : ok(detail())),
    });
    await userEvent.click(await screen.findByRole("button", { name: "查看" }));
    expect(await screen.findByText(/❌ 运费谁承担/)).toBeInTheDocument();
    // 第二题：检索✅ 而词/引用❌ —— 正是"检索对了、生成没达标记
    const row = screen.getByText(/❌ 运费谁承担/).closest("tr") as HTMLElement;
    expect(within(row).getByText(/词❌/)).toBeInTheDocument();
    expect(within(row).getByText(/检索✅/)).toBeInTheDocument();
    // 展开能看到回答原文
    await userEvent.click(screen.getByText(/❌ 运费谁承担/));
    expect(await screen.findByText(/资料里没有相关内容/)).toBeInTheDocument();
  });

  it("报告：拉 Markdown 并展示（可下载）", async () => {    renderPage({
      GET: (url) => (url === P.evalRuns ? ok([run()])
        : url === P.evalRunReport ? ok({ markdown: "# 评测报告\n\n## 总分：18/20（90%）" })
        : ok(detail())),
    });
    await userEvent.click(await screen.findByRole("button", { name: "查看" }));
    await userEvent.click(await screen.findByRole("button", { name: "报告" }));
    expect(await screen.findByText(/# 评测报告/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeInTheDocument();
  });
});

describe("金标准集", () => {
  const openQuestions = async (stubs: Parameters<typeof fakeApi>[0]) => {
    renderPage(stubs);
    await userEvent.click(await screen.findByRole("button", { name: /金标准集/ }));
    return await screen.findByPlaceholderText(/问题，如/);
  };

  it("新增：逗号/顿号切词成数组，POST body 是数组不是字符串", async () => {
    const POST = vi.fn(async () => ok({ ...question, id: 2 }));
    await openQuestions({ POST, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.type(screen.getByPlaceholderText(/问题，如/), "运费谁承担");
    await userEvent.type(screen.getByPlaceholderText(/必须含词/), "商家承担, 补发");
    await userEvent.type(screen.getByPlaceholderText(/期望文档名/), "rag_dirty_doc_01.txt");
    await userEvent.type(screen.getByPlaceholderText(/考察点，如/), "缺失内容");
    await userEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(POST).toHaveBeenCalledWith(P.evalQuestions, {
      body: { question: "运费谁承担", expect_all: ["商家承担", "补发"], expect_any: [],
              cites: ["rag_dirty_doc_01.txt"], category: "缺失内容", note: null,
              enabled: true } }));
  });

  it("空问题不发请求（前端先挡一道）", async () => {
    const POST = vi.fn(async () => ok(question));
    await openQuestions({ POST, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(await screen.findByText("问题不能为空")).toBeInTheDocument();
    expect(POST).not.toHaveBeenCalled();
  });

  it("编辑：表单回填后 PATCH（逗号拼回的词表还原成数组）", async () => {
    const PATCH = vi.fn(async () => ok(question));
    await openQuestions({ PATCH, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(await screen.findByRole("button", { name: "编辑" }));
    expect(screen.getByPlaceholderText(/问题，如/)).toHaveValue("生鲜能七天无理由退货吗");
    await userEvent.clear(screen.getByPlaceholderText(/必须含词/));
    await userEvent.type(screen.getByPlaceholderText(/必须含词/), "不支持, 坏果");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.evalQuestion, {
      params: { path: { qid: 1 } },
      body: { question: "生鲜能七天无理由退货吗", expect_all: ["不支持", "坏果"],
              expect_any: [], cites: ["rag_dirty_doc_01.txt"], category: "错别字干扰",
              note: "校准记录" } }));
  });

  it("停用：PATCH enabled=false（停用而非删除——真题不合用也别把痕迹抹了）", async () => {
    const PATCH = vi.fn(async () => ok({ ...question, enabled: false }));
    await openQuestions({ PATCH, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(await screen.findByRole("button", { name: "停用" }));
    await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.evalQuestion, {
      params: { path: { qid: 1 } }, body: { enabled: false } }));
  });

  it("删除要两步：第一下只出确认，确认后才发 DELETE", async () => {
    const DELETE = vi.fn(async () => ok(undefined));
    await openQuestions({ DELETE, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(await screen.findByRole("button", { name: "删除" }));
    expect(DELETE).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(DELETE).toHaveBeenCalledWith(P.evalQuestion,
      { params: { path: { qid: 1 } } }));
  });

  it("取消删除不发请求", async () => {
    const DELETE = vi.fn(async () => ok(undefined));
    await openQuestions({ DELETE, GET: (url) => (url === P.evalRuns ? ok([]) : undefined) });
    await userEvent.click(await screen.findByRole("button", { name: "删除" }));
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
    expect(DELETE).not.toHaveBeenCalled();
  });
});

// 轮询：running 期间每 1.5s 拉一次，跑到终态就停（此后不再打接口）——
// 与 KbAdmin 的文档轮询同一套约定（终态停顿靠 stopWhen，不靠"轮固定次数"）
it("running 时轮询，跑完即停", async () => {
  let calls = 0;
  const GET = vi.fn(async (url: string) => {
    if (url === P.evalQuestions) return ok([question]);
    calls += 1;
    return ok(calls === 1 ? [run({ status: "running", total: 3, passed: 0 })] : [run()]);
  });
  vi.useFakeTimers();
  render(<EvalAdmin api={fakeApi({ GET })} />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByText(/进行中 3/)).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(1500));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByText("18/20（90%）")).toBeInTheDocument();
  const seen = calls;
  await act(() => vi.advanceTimersByTimeAsync(9000));
  expect(calls).toBe(seen);     // 终态后不应再轮
  vi.useRealTimers();
});
