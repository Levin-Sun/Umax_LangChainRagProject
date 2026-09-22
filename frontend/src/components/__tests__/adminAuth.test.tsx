// 管理鉴权前端面：ApiError 携带 status → is401 判定；AdminBanner 401 给"去登录"入口；
// LoginCard 提交口令（成功回调/失败展示 detail）；logout 先清会话再跳转。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiError, call, is401, logout } from "@/lib/api";
import AdminBanner from "@/components/AdminBanner";
import LoginCard from "@/components/LoginCard";
import { fail, fakeApi, ok } from "@/lib/testkit";
import { P } from "@/lib/paths";

it("call captures HTTP status into ApiError; is401 matches 401 only", async () => {
  await expect(call(Promise.resolve({
    data: undefined, error: { detail: "需要管理员登录" },
    response: new Response("x", { status: 401 }),
  }))).rejects.toMatchObject({ status: 401 });
  expect(is401(new ApiError({}, 401))).toBe(true);
  expect(is401(new ApiError({}, 500))).toBe(false);
  expect(is401(new Error("x"))).toBe(false);
});

it("AdminBanner turns 401 into login prompt, other errors stay plain", () => {
  const { unmount } = render(<AdminBanner error={new ApiError({ detail: "需要管理员登录" }, 401)} />);
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("需要管理员登录");
  expect(screen.getByRole("link", { name: "去登录" })).toHaveAttribute("href", "/admin/login");
  unmount();
  render(<AdminBanner error={new ApiError({ detail: "网关写库炸了" }, 500)} />);
  expect(screen.getByRole("alert")).toHaveTextContent("网关写库炸了");
  expect(screen.queryByRole("link", { name: "去登录" })).not.toBeInTheDocument();
});

it("LoginCard submits token and fires onDone on success", async () => {
  const post = vi.fn(async (_url: string, init: unknown) => {
    expect((init as { body: { token: string } }).body).toEqual({ token: "abc123" });
    return ok(undefined);
  });
  const done = vi.fn();
  render(<LoginCard api={fakeApi({ POST: post })} onDone={done} />);
  await userEvent.type(screen.getByLabelText("管理员口令"), "abc123");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][0]).toBe(P.adminLogin);
  expect(done).toHaveBeenCalled();
});

it("LoginCard shows backend detail on wrong token", async () => {
  const api = fakeApi({ POST: async () => fail("口令错误", 401) });
  render(<LoginCard api={api} onDone={vi.fn()} />);
  await userEvent.type(screen.getByLabelText("管理员口令"), "nope");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("口令错误");
});

it("logout clears session server-side then runs after-hook", async () => {
  const post = vi.fn(async () => ok(undefined));
  const after = vi.fn();
  await logout(fakeApi({ POST: post }), after);
  expect(post).toHaveBeenCalledWith(P.adminLogout, expect.anything());
  expect(after).toHaveBeenCalled();
});
