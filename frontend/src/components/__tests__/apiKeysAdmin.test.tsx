// API key 管理页：断言请求面与"明文一次性"契约——创建响应带 key → 弹窗显示并可复制 →
// 关闭后再无明文；列表只有 key_prefix；停用/删除走 PATCH/DELETE；表单校验拦截不发请求。
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ApiKeysAdmin from "@/components/ApiKeysAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const PLAIN = "umax-abc123def456ghi789jkl012mno345pqr678stu901vwx234";
const rows = [
  { id: 1, name: "钉钉机器人", key_prefix: "umax-abc123de", kb_ids: [7], monthly_token_quota: 100000,
    enabled: true, last_used_at: null, created_at: "2026-10-07T08:00:00" },
  { id: 2, name: "企微助手", key_prefix: "umax-xyz98765", kb_ids: null, monthly_token_quota: null,
    enabled: false, last_used_at: "2026-10-07T09:00:00", created_at: "2026-10-06T08:00:00" },
];

const renderAdmin = (api: ReturnType<typeof fakeApi>) => render(<ApiKeysAdmin api={api} />);
const kbs = [{ id: 7, name: "库A", description: null }, { id: 8, name: "库B", description: null }];

describe("API key 管理", () => {
  it("列表只显打码前缀，无明文 key 字段", async () => {
    renderAdmin(fakeApi({ GET: (u) => (u === P.kb ? ok(kbs) : ok(rows)) }));
    expect(await screen.findByText("umax-abc123de…")).toBeInTheDocument();
    expect(screen.queryByText(PLAIN)).not.toBeInTheDocument();
    expect(screen.getByText("全部")).toBeInTheDocument();          // kb_ids=null → 全库
    expect(screen.getByText("不限")).toBeInTheDocument();          // quota=null → 不限
  });

  it("新建：名称+全库默认，POST 收 {name, kb_ids:null, quota:null}，弹窗显明文且可关", async () => {
    const POST = vi.fn(() => ok({ ...rows[0], name: "新机器人", key: PLAIN }));
    renderAdmin(fakeApi({ GET: (u) => (u === P.kb ? ok(kbs) : ok(rows)), POST }));
    await screen.findByText("钉钉机器人");
    await userEvent.type(screen.getByLabelText("名称"), "新机器人");
    await userEvent.click(screen.getByRole("button", { name: "新建 API key" }));
    await waitFor(() => expect(POST).toHaveBeenCalledWith(P.apiKeys, expect.objectContaining({
      body: { name: "新机器人", kb_ids: null, monthly_token_quota: null },
    })));
    const dialog = await screen.findByRole("dialog", { name: "API key 已创建" });
    expect(within(dialog).getByText(PLAIN)).toBeInTheDocument();   // 明文一次性可见
    await userEvent.click(within(dialog).getByRole("button", { name: "我已保存，关闭" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(PLAIN)).not.toBeInTheDocument();     // 关闭后明文不再出现
  });

  it("指定库模式：勾选库后 POST 收 kb_ids 数组；月配额填了收数字", async () => {
    const POST = vi.fn(() => ok({ ...rows[0], key: PLAIN }));
    renderAdmin(fakeApi({ GET: (u) => (u === P.kb ? ok(kbs) : ok(rows)), POST }));
    await screen.findByText("钉钉机器人");
    await userEvent.type(screen.getByLabelText("名称"), "限定 key");
    await userEvent.click(screen.getByLabelText("全库"));           // 取消全库 → 出库勾选列表
    await userEvent.click(screen.getByLabelText("库 库B"));
    await userEvent.type(screen.getByLabelText("月配额"), "5000");
    await userEvent.click(screen.getByRole("button", { name: "新建 API key" }));
    await waitFor(() => expect(POST).toHaveBeenCalledWith(P.apiKeys, expect.objectContaining({
      body: { name: "限定 key", kb_ids: [8], monthly_token_quota: 5000 },
    })));
  });

  it("指定库模式一个库都不勾：前端拦截不发请求", async () => {
    const POST = vi.fn();
    renderAdmin(fakeApi({ GET: (u) => (u === P.kb ? ok(kbs) : ok(rows)), POST }));
    await screen.findByText("钉钉机器人");
    await userEvent.type(screen.getByLabelText("名称"), "空作用域");
    await userEvent.click(screen.getByLabelText("全库"));
    await userEvent.click(screen.getByRole("button", { name: "新建 API key" }));
    expect(await screen.findByText("指定库模式下至少勾选一个知识库")).toBeInTheDocument();
    expect(POST).not.toHaveBeenCalled();
  });

  it("停用走 PATCH enabled:false；删除走 DELETE", async () => {
    const PATCH = vi.fn(() => ok(rows[0]));
    const DELETE = vi.fn(() => ok(undefined));
    renderAdmin(fakeApi({ GET: (u) => (u === P.kb ? ok(kbs) : ok(rows)), PATCH, DELETE }));
    const row = await screen.findByRole("row", { name: /钉钉机器人/ });
    await userEvent.click(within(row).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.apiKey, expect.objectContaining({
      params: { path: { key_id: 1 } }, body: { enabled: false },
    })));
    const row2 = await screen.findByRole("row", { name: /企微助手/ });
    await userEvent.click(within(row2).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(DELETE).toHaveBeenCalledWith(P.apiKey, expect.objectContaining({
      params: { path: { key_id: 2 } },
    })));
  });
});
