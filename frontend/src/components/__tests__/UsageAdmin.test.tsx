import { render, screen } from "@testing-library/react";
import UsageAdmin from "@/components/UsageAdmin";
import { fakeApi, ok } from "@/lib/testkit";
import { expect, it } from "vitest";

it("aggregates totals and per model rows", async () => {
  const api = fakeApi({ GET: async () => ok([
    { scenario: "chat", model: "qwen3.7-max", calls: 12, prompt_tokens: 100, completion_tokens: 50 },
    { scenario: "embedding", model: "qwen3.7-text-embedding", calls: 30, prompt_tokens: 300, completion_tokens: 0 },
  ]) });
  render(<UsageAdmin api={api} />);
  expect(await screen.findByText("qwen3.7-max")).toBeInTheDocument();
  expect(screen.getByText(/42 次/)).toBeInTheDocument();      // 汇总卡 12+30
  expect(screen.getByText(/450/)).toBeInTheDocument();        // tokens 合计
  expect(screen.queryByText(/qwen3.7-text-rerank/)).not.toBeInTheDocument();
});
