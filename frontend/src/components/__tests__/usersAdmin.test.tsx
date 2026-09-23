// 列表/新建/授权/禁用/自身保护——全部经 fakeApi 打 P 路径常量，断言请求面而非内部状态
// （调用形态与既有 ModelsAdmin.test.tsx 一致：api.X(P.y, { params: { path }, body })）
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UsersAdmin from "@/components/UsersAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok, fail } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const ME = { email: "admin@x.com", name: "admin", role: "admin" as const, kb_ids: null };
const rows = [
  { id: 1, email: "admin@x.com", name: "admin", role: "admin", status: "active",
    created_at: "2026-09-23T08:00:00", kb_ids: null },
  { id: 2, email: "dev@x.com", name: "dev", role: "member", status: "active",
    created_at: "2026-09-23T09:00:00", kb_ids: [7] },
];

const renderAdmin = (api: ReturnType<typeof fakeApi>) =>
  render(<UsersAdmin api={api} me={ME} />);

it("新建用户：POST P.users 带表单字段", async () => {
  const POST = vi.fn(() => ok({ ...rows[0], id: 3 }));
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? ok([{ id: 7, name: "库A", description: null }]) : ok(rows), POST }));
  await screen.findByText("dev@x.com");
  await userEvent.type(screen.getByLabelText("邮箱"), "n@x.com");
  await userEvent.type(screen.getByLabelText("姓名"), "新人");
  await userEvent.type(screen.getByLabelText("初始口令"), "Passw0rd-1");
  await userEvent.click(screen.getByRole("button", { name: "新建用户" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST).toHaveBeenCalledWith(P.users, expect.objectContaining({
    body: expect.objectContaining({ email: "n@x.com", name: "新人", role: "member" }),
  }));
});

it("授权弹窗：勾选后 PUT grants 收到整集合", async () => {
  const PUT = vi.fn(() => ok([7, 8]));
  renderAdmin(fakeApi({
    GET: (u) => u === P.kb ? ok([{ id: 7, name: "库A", description: null }, { id: 8, name: "库B", description: null }]) : ok(rows),
    PUT,
  }));
  const row = await screen.findByRole("row", { name: /dev@x\.com/ });
  await userEvent.click(within(row).getByRole("button", { name: "授权" }));
  const dialog = await screen.findByRole("dialog", { name: /dev@x\.com/ });
  await userEvent.click(within(dialog).getByLabelText("库B"));
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.userGrants, expect.objectContaining({
    params: { path: { user_id: 2 } }, body: { kb_ids: [7, 8] },
  })));
});

it("禁用走 PATCH status；当前登录者行无降级/禁用按钮", async () => {
  const PATCH = vi.fn(() => ok(rows[1]));
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? ok([]) : ok(rows), PATCH }));
  const adminRow = await screen.findByRole("row", { name: /admin@x\.com/ });
  expect(within(adminRow).queryByRole("button", { name: "禁用" })).toBeNull();
  expect(within(adminRow).queryByRole("button", { name: "降级" })).toBeNull();
  const devRow = within(await screen.findByRole("row", { name: /dev@x\.com/ }));
  await userEvent.click(devRow.getByRole("button", { name: "禁用" }));
  await waitFor(() => expect(PATCH).toHaveBeenCalledWith(P.user, expect.objectContaining({
    params: { path: { user_id: 2 } }, body: { status: "disabled" },
  })));
});

it("收编⑯：库列表出错时授权保存禁用，防静默清空整集合", async () => {
  const PUT = vi.fn(() => ok([]));
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? fail("库列表炸了", 500) : ok(rows), PUT }));
  const devRow = within(await screen.findByRole("row", { name: /dev@x\.com/ }));
  await userEvent.click(devRow.getByRole("button", { name: "授权" }));
  const dialog = await screen.findByRole("dialog", { name: /dev@x\.com/ });
  const save = within(dialog).getByRole("button", { name: "保存" });
  expect(save).toBeDisabled();
  await userEvent.click(save); // 禁用态下点击不应发 PUT
  expect(PUT).not.toHaveBeenCalled();
});

it("收编⑯：库列表加载中授权保存禁用", async () => {
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? new Promise(() => {}) : ok(rows) }));
  const devRow = within(await screen.findByRole("row", { name: /dev@x\.com/ }));
  await userEvent.click(devRow.getByRole("button", { name: "授权" }));
  const dialog = await screen.findByRole("dialog", { name: /dev@x\.com/ });
  expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();
});

it("收编⑱：重置口令不足 8 位时保存禁用并给文案", async () => {
  renderAdmin(fakeApi({ GET: (u) => u === P.kb ? ok([]) : ok(rows), PATCH: vi.fn(() => ok(rows[1])) }));
  const devRow = within(await screen.findByRole("row", { name: /dev@x\.com/ }));
  await userEvent.click(devRow.getByRole("button", { name: "重置口令" }));
  await userEvent.type(devRow.getByLabelText("新口令"), "Ab1!");
  await userEvent.type(devRow.getByLabelText("确认新口令"), "Ab1!");
  expect(devRow.getByText("新口令至少 8 位")).toBeInTheDocument();
  expect(devRow.getByRole("button", { name: "保存" })).toBeDisabled();
});
