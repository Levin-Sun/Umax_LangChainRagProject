import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import KbAdmin from "@/components/KbAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { DocOut } from "@/lib/types";

const pending: DocOut = { id: 1, kb_id: 1, name: "ops.txt", status: "pending", error: null, size_bytes: 5, created_at: "2026-10-07T10:00:00+00:00" };
const ready: DocOut = { ...pending, status: "ready" };

describe("KbAdmin 轮询", () => {
  it("polls while pending and stops at ready", async () => {
    let calls = 0;
    const api = fakeApi({
      GET: async (url) => {
        if (url === P.kb) return ok([{ id: 1, name: "运营库", description: null }]);
        if (url === P.kbDocs) {
          calls += 1;
          return ok(calls === 1 ? [pending] : [ready]);
        }
        return undefined;
      },
    });
    vi.useFakeTimers();
    render(<KbAdmin api={api} />);
    await act(() => vi.advanceTimersByTimeAsync(0));           // 首轮：kb 列表（挂载即拉）
    // 裁决（任务5）：doc 轮询 enabled = kbId !== null——必须先选库；fake timers 下
    // userEvent 不可靠 → 原生 DOM click 包进 act
    await act(async () => { screen.getByText("运营库").click(); });
    expect(screen.getByText("排队中")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));        // 第二轮 → ready
    expect(screen.getByText("就绪")).toBeInTheDocument();
    expect(calls).toBe(2);                                     // 裁决：两轮整（原 `calls = 99` 哨兵行与
    await act(() => vi.advanceTimersByTimeAsync(9000));        // 终态后不应再轮，断言不应再变 → 2）
    expect(calls).toBe(2);
    vi.useRealTimers();
  });

  it("failed doc offers reprocess", async () => {
    const failed: DocOut = { ...pending, status: "failed", error: "解析炸了" };
    const reprocess = vi.fn(async () => ok(pending));
    const api = fakeApi({
      GET: async (url) => (url === P.kb ? ok([{ id: 1, name: "库", description: null }])
        : url === P.kbDocs ? ok([failed]) : undefined),
      POST: async (url) => (url === P.docReprocess ? reprocess() : undefined),
    });
    render(<KbAdmin api={api} />);
    // 裁决（任务5）：先选库才会拉 doc 列表，否则永远看不到失败行
    await userEvent.click(await screen.findByText("库"));
    expect(await screen.findByText("解析炸了")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(reprocess).toHaveBeenCalled();
  });

  // 任务7欠账③回归：建库报错须在"未选库"状态下可见（旧版横幅藏在 kbId!==null 块里），
  // 且重试入口清残留旧错（成功一次后横幅消失）
  it("createKb error is visible with no kb selected and clears after a successful retry", async () => {
    let firstAttempt = true;
    const api = fakeApi({
      GET: async (url) => (url === P.kb ? ok(firstAttempt ? [] : [{ id: 2, name: "新库", description: null }])
        : undefined),
      POST: async (url) => {
        if (url !== P.kb) return undefined;
        if (firstAttempt) { firstAttempt = false; return fail("库名已存在", 409); }
        return ok({ id: 2, name: "新库", description: null });
      },
    });
    render(<KbAdmin api={api} />);
    await userEvent.type(screen.getByLabelText("新知识库名"), "新库");
    await userEvent.click(screen.getByRole("button", { name: "建库" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("库名已存在");
    await userEvent.click(screen.getByRole("button", { name: "建库" }));  // 第二次成功
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});

// ---- 批量上传（§A）：多选走批量端点，坏文件只在结果区逐行提示，其余照常入库 ----
it("多选文件 → 走批量端点，坏文件逐行报错", async () => {
  const POST = vi.fn((u: string) => {
    if (u === P.kbDocsBatch) {
      // 服务端逐项结果：一个成功、一个失败（部分成功语义）
      return ok([
        { name: "好的.txt", document: { id: 1, kb_id: 1, name: "好的.txt", status: "ready", error: null, size_bytes: 10, created_at: "2026-10-07T10:00:00+00:00" }, error: null },
        { name: "坏掉.txt", document: null, error: "暂不支持的文件类型：坏掉.txt" },
      ]);
    }
    return ok({});   // 其它 POST（建库等）不应被本用例触发
  });
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }])
      : ok([{ id: 1, kb_id: 1, name: "好的.txt", status: "ready", error: null, size_bytes: 10, created_at: "2026-10-07T10:00:00+00:00" }])),
    POST,
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));   // 先选库：上传区只在选中库后渲染
  await screen.findByText("好的.txt");
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = [new File(["a"], "好的.txt", { type: "text/plain" }),
                 new File(["b"], "坏掉.txt", { type: "text/plain" })];
  await userEvent.upload(input, files);
  await waitFor(() => expect(POST).toHaveBeenCalledWith(P.kbDocsBatch,
    expect.objectContaining({ params: { path: { kb_id: 1 } } })));
  expect(await screen.findByRole("alert")).toHaveTextContent("坏掉.txt：暂不支持的文件类型：坏掉.txt");
});

it("单文件仍走单文件端点（不批量）", async () => {
  const POST = vi.fn(() => ok({ id: 9, kb_id: 1, name: "单个.txt", status: "ready", error: null, size_bytes: 3, created_at: "2026-10-07T10:00:00+00:00" }));
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }]) : ok([])),
    POST,
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  await screen.findByLabelText("上传文档");   // 上传区已在
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, new File(["x"], "单个.txt", { type: "text/plain" }));
  await waitFor(() => expect(POST).toHaveBeenCalledWith(P.kbDocs,
    expect.objectContaining({ params: { path: { kb_id: 1 } } })));
});

// ---- 图表入库（§A）：分块预览标出来源，管理员能看到"这块是图片描述" ----
it("分块抽屉标出图片描述来源", async () => {
  const chunks = [
    { id: 1, chunk_index: 0, content: "售后规则：生鲜不支持七天无理由退货", has_embedding: true, meta: {} },
    { id: 2, chunk_index: 1, content: "【图片内容】退款流程图：含 7 个自然日时限",
      has_embedding: true, meta: { source: "image", image_origin: "word/media/image1.png" } },
  ];
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "图库", description: null }])
      : u === P.kbDocs ? ok([{ id: 1, kb_id: 1, name: "流程.docx", status: "ready", error: null, size_bytes: 100, created_at: "2026-10-07T10:00:00+00:00" }])
      : u === P.docChunks ? ok(chunks) : undefined),
  })} />);
  await screen.findByText("图库");
  await userEvent.click(screen.getByText("图库"));
  await userEvent.click(await screen.findByRole("button", { name: "看切块" }));
  expect(await screen.findByText(/图片描述 · word\/media\/image1.png/)).toBeInTheDocument();
});

// ---- 文档列表：上传时间列 + 删除（二次确认） ----
it("文档表显示上传时间（本地时区到分钟）", async () => {
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }]) : ok([ready])),
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  const row = await screen.findByRole("row", { name: /ops\.txt/ });
  // 2026-10-07T10:00Z 在 UTC+8 是 18:00；断言含"2026"与"10"（精确时刻随运行环境时区）
  expect(within(row).getByText(/2026/)).toBeInTheDocument();
});

it("删除文档：先确认再发 DELETE，取消则不发", async () => {
  const DELETE = vi.fn(() => ok(undefined));
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }]) : ok([ready])),
    DELETE,
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  const row = await screen.findByRole("row", { name: /ops\.txt/ });
  await userEvent.click(within(row).getByRole("button", { name: "删除" }));
  const dialog = await screen.findByRole("dialog", { name: /删除文档 ops\.txt/ });
  await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(DELETE).not.toHaveBeenCalled();
  // 再点一次并确认 → 真的删
  await userEvent.click(within(row).getByRole("button", { name: "删除" }));
  const d2 = await screen.findByRole("dialog", { name: /删除文档 ops\.txt/ });
  await userEvent.click(within(d2).getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(DELETE).toHaveBeenCalledWith(P.doc, expect.objectContaining({
    params: { path: { doc_id: 1 } },
  })));
});

// ---- 上传入口优化（体验反馈：原生控件两段式"选择文件/未选择任何文件"既冗余又像两个入口）----
it("上传区只有一个可见入口（按钮），原生控件不渲染多余文本", async () => {
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }]) : ok([])),
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  const zone = await screen.findByRole("group", { name: "文档上传" });
  expect(within(zone).getByRole("button", { name: "选择文件" })).toBeInTheDocument();
  // 原生 file 控件仍在（无障碍/测试入口），但不渲染可见文本
  const input = within(zone).getByLabelText("上传文档") as HTMLInputElement;
  expect(input).toHaveAttribute("type", "file");
  expect(zone.textContent).not.toContain("未选择任何文件");
  expect(within(zone).getByText(/拖到这里/)).toBeInTheDocument();
});

it("拖拽文件到上传区即上传（多个走批量端点）", async () => {
  const POST = vi.fn((u: string) => (u === P.kbDocsBatch
    ? ok([{ name: "拖一.txt", document: { id: 1, kb_id: 1, name: "拖一.txt", status: "ready", error: null, size_bytes: 5, created_at: "2026-10-07T10:00:00+00:00" }, error: null }])
    : ok({})));
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }]) : ok([])),
    POST,
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  const zone = await screen.findByRole("group", { name: "文档上传" });
  const files = [new File(["a"], "拖一.txt", { type: "text/plain" }),
                 new File(["b"], "拖二.txt", { type: "text/plain" })];
  fireEvent.drop(zone, { dataTransfer: { files } });
  await waitFor(() => expect(POST).toHaveBeenCalledWith(P.kbDocsBatch,
    expect.objectContaining({ params: { path: { kb_id: 1 } } })));
});

// ---- 大小单位（体验反馈）：≥0.1MB 用 MB 一位小数，不足才用 KB ----
it("大小按 0.1MB 分档：大文件用 MB，小文件用 KB", async () => {
  const mk = (id: number, name: string, size: number): DocOut => ({
    id, kb_id: 1, name, status: "ready", error: null, size_bytes: size,
    created_at: "2026-10-07T10:00:00+00:00" });
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }])
      : ok([mk(1, "大.docx", 9_017_941), mk(2, "中.pdf", 104_858), mk(3, "小.md", 1234)])),
  })} />);
  await screen.findByText("库A");
  await userEvent.click(screen.getByText("库A"));
  const row = (n: string) => screen.findByRole("row", { name: new RegExp(n) });
  expect(within(await row("大\\.docx")).getByText("8.6 MB")).toBeInTheDocument();
  expect(within(await row("中\\.pdf")).getByText("0.1 MB")).toBeInTheDocument();   // 恰在 0.1MB 档
  expect(within(await row("小\\.md")).getByText("1.2 KB")).toBeInTheDocument();
  // 看切块与删除同处一个 nowrap 容器（保证并排一行，不叠两行）
  const big = await row("大\\.docx");
  const actions = big.querySelector("span.inline-flex.whitespace-nowrap");
  expect(actions).not.toBeNull();
  expect(within(actions as HTMLElement).getByRole("button", { name: "看切块" })).toBeInTheDocument();
  expect(within(actions as HTMLElement).getByRole("button", { name: "删除" })).toBeInTheDocument();
});
