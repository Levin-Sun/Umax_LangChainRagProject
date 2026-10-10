// 查询参数装配在 init.params.query（URL 不带串）
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditAdmin from "@/components/AuditAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { expect, it, vi } from "vitest";

const auditRow = (id: number) => ({
  id, user_email: "dev@x.com", action: "login_failed", target_type: null,
  target_id: null, detail: {}, ip: "127.0.0.1", created_at: "2026-09-23T10:00:00" });

it("查询与翻页：query 参数逐次装配", async () => {
  // fakeApi 契约是 (url, init?: unknown)——strictFunctionTypes 下窄参签名不可赋值（tsc 实测），
  // 故入参收 unknown 后内部拆包，断言面与 brief 完全一致
  const GET = vi.fn((_u: string, init?: unknown) => {
    const offset = (init as { params?: { query?: { offset?: number } } } | undefined)?.params?.query?.offset ?? 0;
    // 满页（PAGE+1 条）：多出的那条只用于判"还有下一页"，末页置灰靠它。
    // 只有第一条保留 login_failed，其余改个动作名——否则行选择器会撞上 51 行。
    return ok(Array.from({ length: 51 }, (_, i) =>
      i === 0 ? auditRow(offset + 1) : { ...auditRow(offset + i + 1), action: "login_ok" }));
  });
  render(<AuditAdmin api={fakeApi({ GET })} />);
  await screen.findByRole("row", { name: /login_failed/ });
  // 自绘下拉：先展开再点选项（原生 select 的 selectOptions 不再适用）
  await userEvent.click(screen.getByLabelText("动作"));
  await userEvent.click(await screen.findByRole("option", { name: "login_failed" }));
  await userEvent.click(screen.getByRole("button", { name: "查询" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ action: "login_failed", limit: 51, offset: 0 }) },
  })));
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ offset: 50 }) },
  })));
});

// 已知限制收口：审计接口没有 total，用"多取一条"判末页——置灰才不会被当成坏了
it("末页把「下一页」置灰：只回不满页时没有下一页", async () => {
  const GET = vi.fn(() => ok([auditRow(1), auditRow(2)]));
  render(<AuditAdmin api={fakeApi({ GET })} />);
  expect(await screen.findAllByRole("row", { name: /login_failed/ })).toHaveLength(2);
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  // 只显示 2 条（多取的那条不存在时不该多渲染）
  expect(screen.getAllByRole("row")).toHaveLength(3);      // 表头 + 2 行
});

it("收编⑲：条件未变仍在首页点查询也强制重取（查询按钮不像坏了）", async () => {
  const GET = vi.fn(() => ok([auditRow(1)]));
  render(<AuditAdmin api={fakeApi({ GET })} />);
  await screen.findByRole("row", { name: /login_failed/ });
  const before = GET.mock.calls.length;
  // 不改任何过滤条件，停在 offset 0 再点查询——旧实现 setApplied 值全等 → useAsync 依赖不变 → 不重取
  await userEvent.click(screen.getByRole("button", { name: "查询" }));
  await waitFor(() => expect(GET.mock.calls.length).toBeGreaterThan(before));
});

it("空结果渲染空态不渲染表行", async () => {
  render(<AuditAdmin api={fakeApi({ GET: () => ok([]) })} />);
  await screen.findByText("没有匹配的审计记录");
  expect(screen.queryAllByRole("row", { name: /login_/ })).toHaveLength(0);
});
