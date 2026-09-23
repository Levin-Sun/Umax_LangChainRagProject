// 查询参数装配在 init.params.query（URL 不带串）
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditAdmin from "@/components/AuditAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const auditRow = (id: number) => ({
  id, user_email: "dev@x.com", action: "login_failed", target_type: null,
  target_id: null, detail: {}, ip: "127.0.0.1", created_at: "2026-09-23T10:00:00" });

it("查询与翻页：query 参数逐次装配", async () => {
  // fakeApi 契约是 (url, init?: unknown)——strictFunctionTypes 下窄参签名不可赋值（tsc 实测），
  // 故入参收 unknown 后内部拆包，断言面与 brief 完全一致
  const GET = vi.fn((_u: string, init?: unknown) => {
    const offset = (init as { params?: { query?: { offset?: number } } } | undefined)?.params?.query?.offset ?? 0;
    return ok([auditRow(offset + 1)]);
  });
  render(<AuditAdmin api={fakeApi({ GET })} />);
  await screen.findByRole("row", { name: /login_failed/ });
  await userEvent.selectOptions(screen.getByLabelText("动作"), "login_failed");
  await userEvent.click(screen.getByRole("button", { name: "查询" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ action: "login_failed", limit: 50, offset: 0 }) },
  })));
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(GET).toHaveBeenCalledWith(P.audit, expect.objectContaining({
    params: { query: expect.objectContaining({ offset: 50 }) },
  })));
});

it("空结果渲染空态不渲染表行", async () => {
  render(<AuditAdmin api={fakeApi({ GET: () => ok([]) })} />);
  await screen.findByText("没有匹配的审计记录");
  expect(screen.queryAllByRole("row", { name: /login_/ })).toHaveLength(0);
});
