import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ModelsAdmin from "@/components/ModelsAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { ModelOut } from "@/lib/types";
import { expect, it, vi } from "vitest";

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

it("401 from protected list offers login entry", async () => {
  const api = fakeApi({ GET: async () => fail("需要管理员登录", 401) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("需要管理员登录");
  expect(screen.getByRole("link", { name: "去登录" })).toHaveAttribute("href", "/admin/login");
});

// 登记表单中文提示：可访问名=中文 label（替代裸字段名 aria-label）
it("register form fields carry Chinese labels", async () => {
  const api = fakeApi({ GET: async () => ok([]) });
  render(<ModelsAdmin api={api} />);
  for (const label of ["场景", "厂商", "接口地址", "API 密钥", "模型名", "回退优先级"]) {
    expect(await screen.findByLabelText(label)).toBeInTheDocument();
  }
  // 占位提示保留中英对照，便于对照 API 字段
  expect(screen.getByPlaceholderText(/provider/)).toBeInTheDocument();
});

// 任务7欠账②回归：启停/删除失败要进 ErrorBanner（旧实现裸 await——rejection 无人接，
// UI 静默失败）；busy 防重入由同一次点击只发一请求间接锁定
it("surfaces toggle failure in banner instead of failing silently", async () => {
  const patch = vi.fn(async (..._args: unknown[]) => fail("网关写库炸了", 500));
  const api = fakeApi({
    GET: async (url) => (url === P.models ? ok([m]) : undefined),
    PATCH: async (url, init) => (url === P.model ? patch(url, init) : undefined),
  });
  render(<ModelsAdmin api={api} />);
  const sw = await screen.findByRole("switch", { name: "启用 qwen3.7-max" });
  await userEvent.click(sw);
  expect(await screen.findByRole("alert")).toHaveTextContent("网关写库炸了");
  expect(patch).toHaveBeenCalledTimes(1);
});
