import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ModelsAdmin from "@/components/ModelsAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { ModelOut } from "@/lib/types";
import { describe, expect, it, vi } from "vitest";

const m: ModelOut = { id: 3, scenario: "chat", provider: "bailian", base_url: "https://x",
  model_name: "qwen3.7-max", capabilities: {}, is_default: true, fallback_rank: 0,
  enabled: true, api_key_masked: "****7f3a" };

it("shows masked key as-is, never reconstructed", async () => {
  const api = fakeApi({ GET: async (url) => (url === P.models ? ok([m]) : undefined) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByText("****7f3a")).toBeInTheDocument();
});

it("toggles enabled via PATCH", async () => {
  const patch = vi.fn(async (..._args: unknown[]) => ok({ ...m, enabled: false }));
  const api = fakeApi({
    GET: async (url) => (url === P.models ? ok([m]) : undefined),
    PATCH: async (url, init) => (url === P.model ? patch(url, init) : undefined),
  });
  render(<ModelsAdmin api={api} />);
  await userEvent.click(await screen.findByRole("switch", { name: "启用 qwen3.7-max" }));
  expect(patch).toHaveBeenCalledWith(P.model, expect.objectContaining({
    params: { path: { model_id: 3 } }, body: { enabled: false },
  }));
});

it("validates required fields before POST", async () => {
  const post = vi.fn(async () => ok(m));
  const api = fakeApi({ GET: async () => ok([]), POST: post });
  render(<ModelsAdmin api={api} />);
  await userEvent.click(screen.getByRole("button", { name: "提交登记" }));
  expect(await screen.findByText(/必填/)).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it("renders 503 detail from backend (gateway secret missing)", async () => {
  const api = fakeApi({ GET: async () => fail("未配置主密钥 GATEWAY_SECRET，无法管理模型 key", 503) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("GATEWAY_SECRET");
});
