import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
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
