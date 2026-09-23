import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
import { call, setUnauthorizedHandler } from "@/lib/api";
import { P } from "@/lib/paths";
import { fail, fakeApi } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const fill = async (oldPw: string, newPw: string, confirm: string) => {
  await userEvent.type(screen.getByLabelText("旧口令"), oldPw);
  await userEvent.type(screen.getByLabelText("新口令"), newPw);
  await userEvent.type(screen.getByLabelText("确认新口令"), confirm);
  await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
};

it("旧口令错：401 detail 上屏", async () => {
  const POST = vi.fn(() => fail("邮箱或口令错误", 401));
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await fill("wrong", "New-Pass-9", "New-Pass-9");
  expect(await screen.findByText("邮箱或口令错误")).toBeInTheDocument();
  expect(POST).toHaveBeenCalledWith(P.authChangePassword, expect.objectContaining({
    body: { old_password: "wrong", new_password: "New-Pass-9" },
  }));
});

it("两次新口令不一致：前端拦截，不发请求", async () => {
  const POST = vi.fn();
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await fill("Old-Pass-9", "New-Pass-9", "New-Pass-0");
  expect(await screen.findByText("两次输入的新口令不一致")).toBeInTheDocument();
  expect(POST).not.toHaveBeenCalled();
});

it("收编⑱：新口令短于 8 位前端拦截并上文案，不发请求", async () => {
  const POST = vi.fn();
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await fill("Old-Pass-9", "Ab1!", "Ab1!");  // 两遍一致但不足 8 位
  expect(await screen.findByText("新口令至少 8 位")).toBeInTheDocument();
  expect(POST).not.toHaveBeenCalled();
});

it("收编⑰：提交进行中回车重入不再发第二个请求（busy 防护）", async () => {
  let settle: ((v: unknown) => void) | undefined;
  const POST = vi.fn(() => new Promise((r) => { settle = r; }));
  render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
  await userEvent.type(screen.getByLabelText("旧口令"), "Old-Pass-9");
  await userEvent.type(screen.getByLabelText("新口令"), "New-Pass-9");
  await userEvent.type(screen.getByLabelText("确认新口令"), "New-Pass-9");
  await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
  expect(POST).toHaveBeenCalledOnce();
  // busy=true、POST 仍 pending：回车再次触发 form.submit()，旧实现会并发第二个请求
  await userEvent.type(screen.getByLabelText("确认新口令"), "{Enter}");
  expect(POST).toHaveBeenCalledOnce();
  settle?.({ data: undefined, error: undefined, response: new Response() });
});

it("终审收口：改密 401 只上横幅，不触发全局『跳登录页』钩子", async () => {
  // 旧口令打错=表单级错误（后端 401 "旧口令错误"）。全局 401 钩子会把已登录用户整页
  // 拽到 /admin/login，横幅文案一眼看不到，人还留在登录态里——改密这条请求必须显式豁免。
  const h = vi.fn();
  setUnauthorizedHandler(h);
  try {
    const POST = vi.fn(() => fail("旧口令错误", 401));
    render(<ChangePasswordDialog api={fakeApi({ POST })} onClose={vi.fn()} />);
    await fill("wrong", "New-Pass-9", "New-Pass-9");
    expect(await screen.findByText("旧口令错误")).toBeInTheDocument();
    expect(h).not.toHaveBeenCalled();
    // 对照：同一个钩子对普通 401 照旧生效——否则上面的 not.toHaveBeenCalled 是钩子失灵的假通过
    await expect(call(fail("需要登录", 401))).rejects.toMatchObject({ status: 401 });
    expect(h).toHaveBeenCalledTimes(1);
  } finally {
    setUnauthorizedHandler(() => {});
  }
});
