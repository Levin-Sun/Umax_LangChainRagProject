import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import KbAdmin from "@/components/KbAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { DocOut } from "@/lib/types";

const pending: DocOut = { id: 1, kb_id: 1, name: "ops.txt", status: "pending", error: null, size_bytes: 5 };
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
    await userEvent.click(screen.getByRole("button", { name: "重试入库" }));
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
        { name: "好的.txt", document: { id: 1, kb_id: 1, name: "好的.txt", status: "ready", error: null, size_bytes: 10 }, error: null },
        { name: "坏掉.txt", document: null, error: "暂不支持的文件类型：坏掉.txt" },
      ]);
    }
    return ok({});   // 其它 POST（建库等）不应被本用例触发
  });
  render(<KbAdmin api={fakeApi({
    GET: (u) => (u === P.kb ? ok([{ id: 1, name: "库A", description: null }])
      : ok([{ id: 1, kb_id: 1, name: "好的.txt", status: "ready", error: null, size_bytes: 10 }])),
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
  const POST = vi.fn(() => ok({ id: 9, kb_id: 1, name: "单个.txt", status: "ready", error: null, size_bytes: 3 }));
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
