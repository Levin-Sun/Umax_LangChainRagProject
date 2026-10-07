// 配置中心页：加载回显、只发改动字段、恢复默认走 null、警告条上屏、未改动时保存禁用。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsAdmin from "@/components/SettingsAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const snap = {
  values: { chat_system_prompt: "系统提示词", chat_miss_answer: "资料里没有相关内容，无法回答。",
            vision_prompt: "描述图片", recall_k: 10, rerank_top_n: 5, min_sim: 0.15,
            chunk_target: 300, chunk_min: 60 },
  defaults: { chat_system_prompt: "系统提示词", chat_miss_answer: "资料里没有相关内容，无法回答。",
              vision_prompt: "描述图片", recall_k: 10, rerank_top_n: 5, min_sim: 0.15,
              chunk_target: 300, chunk_min: 60 },
  overridden: [] as string[], warnings: [] as string[],
  labels: { chat_system_prompt: "问答系统提示词", recall_k: "召回条数" },
  help: { chat_system_prompt: "决定回答的语气与引用规则" },
};

const renderPage = (cfg = snap) =>
  render(<SettingsAdmin api={fakeApi({ GET: (u) => (u === P.settings ? ok(cfg) : ok([])) })} />);

describe("配置中心", () => {
  it("回显生效值与说明；未改动时保存禁用", async () => {
    renderPage();
    expect(await screen.findByLabelText("问答系统提示词")).toHaveValue("系统提示词");
    expect(screen.getByLabelText("召回条数")).toHaveValue(10);
    expect(screen.getByText("决定回答的语气与引用规则")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("改一项 → PUT 只带该项；保存后提示已保存", async () => {
    const PUT = vi.fn(() => ok(snap));
    render(<SettingsAdmin api={fakeApi({ GET: (u) => (u === P.settings ? ok(snap) : ok([])), PUT })} />);
    const input = await screen.findByLabelText("召回条数");
    await userEvent.clear(input);
    await userEvent.type(input, "12");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.settings, { body: { recall_k: 12 } }));
    expect(await screen.findByText("已保存。")).toBeInTheDocument();
  });

  it("已自定义字段提供恢复默认（PUT 传 null）", async () => {
    const PUT = vi.fn(() => ok({ ...snap, overridden: [] }));
    const overridden = { ...snap, values: { ...snap.values, recall_k: 20 }, overridden: ["recall_k"] };
    render(<SettingsAdmin api={fakeApi({ GET: (u) => (u === P.settings ? ok(overridden) : ok([])), PUT })} />);
    expect(await screen.findByText("已自定义")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.settings, { body: { recall_k: null } }));
  });

  it("跨字段警告上屏", async () => {
    renderPage({ ...snap, warnings: ["chunk_min（400）大于 chunk_target（150）：短块会全部并入首块，建议 min ≤ target。"] });
    expect(await screen.findByText(/chunk_min（400）大于 chunk_target/)).toBeInTheDocument();
  });
});
