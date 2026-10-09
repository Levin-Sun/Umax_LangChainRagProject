import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ModelsAdmin from "@/components/ModelsAdmin";
import { P } from "@/lib/paths";
import { fail, fakeApi, ok } from "@/lib/testkit";
import type { ModelCatalog, ModelOut } from "@/lib/types";
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
  await userEvent.click(screen.getByText(/高级：手动登记/));   // 手动登记已折叠，长尾出口仍可达
  await userEvent.click(screen.getByRole("button", { name: "提交登记" }));
  expect(await screen.findByText(/必填/)).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

// ---- 一键配齐 + 能力矩阵（客户认知成本：不需要懂四类场景）----
const CATALOG: ModelCatalog = {
  catalog_version: "2026.10.10",
  updated_at: "2026-10-10",
  capability_labels: { chat: "对话问答", embedding: "语义检索", vision: "看图提问", rerank: "结果精排" },
  capability_miss: { chat: "答不出完整回答", embedding: "同义不同词问不出来",
                     vision: "图片被忽略（可选）", rerank: "答案略逊（可选）" },
  vendors: [
    {
      id: "bailian", name: "阿里百炼", aliases: ["百炼", "dashscope"], pinyin: ["abl", "alibailian"],
      verified: true, verified_at: "2026-10-10",
      profiles: [{ id: "metered", name: "按量计费", base_url: "https://ds/compatible-mode/v1" },
                 { id: "native", name: "原生端点（精排专用）", base_url: "https://ds/api/v1" }],
      capabilities: [
        { key: "chat", scenario: "chat", model: "qwen-plus", profile: "metered" },
        { key: "embedding", scenario: "embedding", model: "text-embedding-v4", profile: "metered", dim: 1024 },
      ],
    },
    {
      id: "deepseek", name: "DeepSeek", aliases: ["de"], pinyin: [], verified: false,
      profiles: [{ id: "metered", name: "按量计费", base_url: "https://api.deepseek.com/v1" }],
      capabilities: [{ key: "chat", scenario: "chat", model: "deepseek-chat", profile: "metered" }],
      note: "只提供对话，不提供向量",
    },
  ],
};

const withCatalog = (extra: Record<string, unknown> = {}) => fakeApi({
  GET: (u: string) => (u === P.modelCatalog ? ok(CATALOG) : u === P.models ? ok([]) : undefined),
  ...extra,
});

it("一键配齐：选厂商 → 填 key → POST bundle，厂商的原始报错直接上屏", async () => {
  const POST = vi.fn(() => ok({
    vendor: "阿里百炼",
    items: [
      { capability: "chat", scenario: "chat", model: "qwen-plus", base_url: "https://ds",
        action: "created", ok: true, detail: null },
      { capability: "embedding", scenario: "embedding", model: "text-embedding-v4", base_url: "https://ds",
        action: "updated", ok: false, detail: "RuntimeError: 模型名不存在" },
    ],
  }));
  render(<ModelsAdmin api={withCatalog({ POST }) as never} />);
  await userEvent.click(await screen.findByLabelText("厂商（可搜索）"));
  await userEvent.click(await screen.findByRole("option", { name: /阿里百炼/ }));
  expect(screen.queryByRole("listbox")).toBeNull();     // 选完即关（不用再点别处）
  await userEvent.type(screen.getByLabelText("厂商 API 密钥"), "sk-test");
  await userEvent.click(screen.getByRole("button", { name: "一键配齐并测试" }));
  expect(POST).toHaveBeenCalledWith(P.modelsBundle, expect.objectContaining({
    body: { vendor_id: "bailian", api_key: "sk-test", capabilities: ["chat", "embedding"], test: true },
  }));
  expect(await screen.findByText(/模型名不存在/)).toBeInTheDocument();
  expect(screen.getByText(/更新了 key/)).toBeInTheDocument();
});

it("厂商下拉：聚焦即列出全部，输入按别名与拼音过滤", async () => {
  render(<ModelsAdmin api={withCatalog() as never} />);
  const box = await screen.findByLabelText("厂商（可搜索）");
  await userEvent.click(box);
  expect(await screen.findByRole("option", { name: /DeepSeek/ })).toBeInTheDocument();
  await userEvent.type(box, "abl");                    // 拼音命中
  expect(screen.getByRole("option", { name: /阿里百炼/ })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /DeepSeek/ })).toBeNull();
});

it("能力不足的厂商被当场点出来（不用等入库失败）", async () => {
  render(<ModelsAdmin api={withCatalog() as never} />);
  await userEvent.click(await screen.findByLabelText("厂商（可搜索）"));
  await userEvent.click(await screen.findByRole("option", { name: /DeepSeek/ }));
  expect(await screen.findByText(/这家不提供：语义检索/)).toBeInTheDocument();
});

it("能力矩阵：已配的显示模型名，未配的说清代价", async () => {
  const rows: ModelOut[] = [{ ...m, scenario: "embedding", model_name: "text-embedding-v4" }];
  const api = fakeApi({
    GET: (u: string) => (u === P.modelCatalog ? ok(CATALOG) : u === P.models ? ok(rows) : undefined),
  });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByText("已配 text-embedding-v4")).toBeInTheDocument();
  expect(screen.getAllByText("未配置").length).toBeGreaterThan(0);
  expect(screen.getByText(/答不出完整回答/)).toBeInTheDocument();
});

// 用户反馈：列表没有厂商列，按厂商找模型费劲；同一个厂商还有多条档案（兼容模式 / 原生端点）。
it("列表显示厂商与接入档案，便于按厂商找模型", async () => {
  const rows: ModelOut[] = [
    { ...m, provider: "阿里百炼", base_url: "https://ds/compatible-mode/v1", model_name: "qwen-plus" },
    { ...m, id: 9, scenario: "rerank", provider: "阿里百炼", base_url: "https://ds/api/v1",
      model_name: "qwen3.7-text-rerank" },
  ];
  const api = fakeApi({
    GET: (u: string) => (u === P.modelCatalog ? ok(CATALOG) : u === P.models ? ok(rows) : undefined),
  });
  render(<ModelsAdmin api={api} />);
  // 限定在"模型配置列表"里找：能力矩阵那张表也会出现模型名，不限定会撞行
  const listTable = within(await screen.findByRole("table", { name: "模型配置列表" }));
  const rerankRow = await listTable.findByRole("row", { name: /qwen3\.7-text-rerank/ });
  expect(within(rerankRow).getByText("阿里百炼")).toBeInTheDocument();
  // 档案名来自标本反查：光有厂商名分不清"精排走的是原生端点"
  expect(within(rerankRow).getByText("原生端点（精排专用）")).toBeInTheDocument();
});

// 用户反馈：需要一个"修改"入口（换 key / 换模型）；否则改 key 只能删了重登，那一瞬该能力没有模型可用。
it("编辑模型：改模型名时留空密钥 → PATCH 不带 api_key", async () => {
  const PATCH = vi.fn((..._a: unknown[]) => ok(m));
  const api = fakeApi({
    GET: (u: string) => (u === P.modelCatalog ? ok(CATALOG) : u === P.models ? ok([m]) : undefined),
    PATCH,
  });
  render(<ModelsAdmin api={api} />);
  const listTable = within(await screen.findByRole("table", { name: "模型配置列表" }));
  const row = await listTable.findByRole("row", { name: /qwen3\.7-max/ });
  await userEvent.click(within(row).getByRole("button", { name: "编辑" }));
  const dialog = await screen.findByRole("dialog", { name: /编辑模型 qwen3\.7-max/ });
  await userEvent.clear(within(dialog).getByLabelText("模型名"));
  await userEvent.type(within(dialog).getByLabelText("模型名"), "qwen-plus");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.model, expect.objectContaining({
    params: { path: { model_id: 3 } },
    body: expect.objectContaining({ model_name: "qwen-plus", scenario: "chat" }),
  })));
  const body = (PATCH.mock.calls[0][1] as { body: Record<string, unknown> }).body;
  expect(body).not.toHaveProperty("api_key");     // 留空＝不修改（库里只有密文，打码值不能回写）
});

it("编辑模型：填了密钥才带上 api_key", async () => {
  const PATCH = vi.fn((..._a: unknown[]) => ok(m));
  const api = fakeApi({
    GET: (u: string) => (u === P.modelCatalog ? ok(CATALOG) : u === P.models ? ok([m]) : undefined),
    PATCH,
  });
  render(<ModelsAdmin api={api} />);
  const listTable = within(await screen.findByRole("table", { name: "模型配置列表" }));
  const row = await listTable.findByRole("row", { name: /qwen3\.7-max/ });
  await userEvent.click(within(row).getByRole("button", { name: "编辑" }));
  const dialog = await screen.findByRole("dialog", { name: /编辑模型 qwen3\.7-max/ });
  await userEvent.type(within(dialog).getByLabelText("密钥（留空不改）"), "sk-new");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.model, expect.objectContaining({
    body: expect.objectContaining({ api_key: "sk-new" }),
  })));
});

it("renders 503 detail from backend (gateway secret missing)", async () => {
  const api = fakeApi({ GET: async () => fail("未配置主密钥 GATEWAY_SECRET，无法管理模型 key", 503) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("GATEWAY_SECRET");
});

it("401 from protected list offers login entry", async () => {
  const api = fakeApi({ GET: async () => fail("需要登录", 401) });
  render(<ModelsAdmin api={api} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("需要登录");
  expect(screen.getByRole("link", { name: "去登录" })).toHaveAttribute("href", "/admin/login");
});

// 登记表单中文提示：可访问名=中文 label（替代裸字段名 aria-label）
it("register form fields carry Chinese labels", async () => {
  const api = fakeApi({ GET: async () => ok([]) });
  render(<ModelsAdmin api={api} />);
  await userEvent.click(await screen.findByText(/高级：手动登记/));
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
