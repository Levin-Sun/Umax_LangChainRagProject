import { render, screen, within } from "@testing-library/react";
import UsageAdmin from "@/components/UsageAdmin";
import { fakeApi, ok } from "@/lib/testkit";
import { P } from "@/lib/paths";
import { expect, it } from "vitest";

it("aggregates totals and per model rows", async () => {
  const api = fakeApi({ GET: (u) => (u === P.usageUsers ? ok([]) : ok([
    { scenario: "chat", model: "qwen3.7-max", calls: 12, prompt_tokens: 100, completion_tokens: 50 },
    { scenario: "embedding", model: "qwen3.7-text-embedding", calls: 30, prompt_tokens: 300, completion_tokens: 0 },
  ])) });
  render(<UsageAdmin api={api} />);
  expect(await screen.findByText("qwen3.7-max")).toBeInTheDocument();
  expect(screen.getByText(/42 次/)).toBeInTheDocument();      // 汇总卡 12+30
  expect(screen.getByText(/450/)).toBeInTheDocument();        // tokens 合计
  expect(screen.queryByText(/qwen3.7-text-rerank/)).not.toBeInTheDocument();
});

// ---- 按人用量视图（§C 配额）：用量/上限/状态标记 ----
it("按人视图显用量、上限与超限标记", async () => {
  const users = [
    { id: 1, email: "big@x.com", name: "大户", daily_used: 900, daily_limit: 1000,
      monthly_used: 900, monthly_limit: 1000, near_limit: true, exceeded: false, warn_ratio: 0.8 },
    { id: 2, email: "out@x.com", name: "超额", daily_used: 50, daily_limit: 50,
      monthly_used: 50, monthly_limit: null, near_limit: false, exceeded: true, warn_ratio: 0.8 },
  ];
  render(<UsageAdmin api={fakeApi({
    GET: (u) => (u === P.usageUsers ? ok(users) : ok([])),
  })} />);
  expect(await screen.findByText("按人用量（今日 / 本月）")).toBeInTheDocument();
  const bigRow = await screen.findByRole("row", { name: /big@x\.com/ });
  expect(within(bigRow).getByText("接近上限")).toBeInTheDocument();
  expect(within(bigRow).getByText("1000 / 1000")).toBeInTheDocument();
  const outRow = await screen.findByRole("row", { name: /out@x\.com/ });
  expect(within(outRow).getByText("已超限")).toBeInTheDocument();
  expect(within(outRow).getByText("50 / 不限")).toBeInTheDocument();
});
