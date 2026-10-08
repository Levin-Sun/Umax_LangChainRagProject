// 首登强改密门闸（前端侧）：me.must_change_password=true 时全站罩强制改密框——
// 无取消、遮罩不可关；改密成功 refresh 拉新 me，标记翻false 后框自动消失露出业务页。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { AuthProvider } from "@/lib/auth";
import { MustChangeGate } from "@/components/Providers";
import { fail, fakeApi, ok } from "@/lib/testkit";
import { P } from "@/lib/paths";
import type { AuthMe } from "@/lib/types";

const FLAGGED: AuthMe = { email: "a@x.com", name: "阿管", role: "admin", kb_ids: null, must_change_password: true };
const SETTLED: AuthMe = { ...FLAGGED, must_change_password: false };

const meApi = (me: AuthMe) => fakeApi({ GET: (u) => (u.includes(P.authMe) ? ok(me) : ok([])) });

it("must_change_password=true：罩强制改密框（无取消按钮）", async () => {
  render(<AuthProvider client={meApi(FLAGGED) as never}><MustChangeGate client={meApi(FLAGGED) as never} /><div>业务页</div></AuthProvider>);
  expect(await screen.findByRole("dialog", { name: "首次登录修改口令" })).toBeInTheDocument();
  expect(screen.getByText("首次登录，请修改初始口令")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
});

it("改密成功：refresh 拉新 me，框消失露出业务页", async () => {
  let me = FLAGGED;
  const api = fakeApi({
    GET: (u) => (u.includes(P.authMe) ? ok(me) : ok([])),
    POST: (u) => { if (u === P.authChangePassword) { me = SETTLED; return ok(undefined); } return ok(undefined); },
  });
  render(<AuthProvider client={api as never}><MustChangeGate client={api as never} /><div>业务页</div></AuthProvider>);
  await screen.findByRole("dialog", { name: "首次登录修改口令" });
  await userEvent.type(screen.getByLabelText("旧口令"), "Old-Pass-123");
  await userEvent.type(screen.getByLabelText("新口令"), "New-Pass-456");
  await userEvent.type(screen.getByLabelText("确认新口令"), "New-Pass-456");
  await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByText("业务页")).toBeInTheDocument();
});

it("改密失败（旧口令错）：错误留在框内，门闸不解除", async () => {
  const api = fakeApi({
    GET: (u) => (u.includes(P.authMe) ? ok(FLAGGED) : ok([])),
    POST: () => fail("旧口令错误", 401),
  });
  render(<AuthProvider client={api as never}><MustChangeGate client={api as never} /><div>业务页</div></AuthProvider>);
  await screen.findByRole("dialog", { name: "首次登录修改口令" });
  await userEvent.type(screen.getByLabelText("旧口令"), "wrong");
  await userEvent.type(screen.getByLabelText("新口令"), "New-Pass-456");
  await userEvent.type(screen.getByLabelText("确认新口令"), "New-Pass-456");
  await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
  expect(await screen.findByText("旧口令错误")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "首次登录修改口令" })).toBeInTheDocument();
});

it("must_change_password=false：不罩框", async () => {
  render(<AuthProvider client={meApi(SETTLED) as never}><MustChangeGate client={meApi(SETTLED) as never} /><div>业务页</div></AuthProvider>);
  await waitFor(() => expect(screen.getByText("业务页")).toBeInTheDocument());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
