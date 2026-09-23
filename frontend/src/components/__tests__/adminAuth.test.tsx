// 登录链路前端面：ApiError 携带 status → is401 判定；AdminBanner 401 给"去登录"入口；
// LoginCard 提交邮箱+口令（useAuth.login → 成功 onDone(me)、失败上屏 detail 含 429 限流文案）；
// 登录页按角色分流：admin→/admin/kb，member→聊天。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, call, is401 } from "@/lib/api";
import AdminBanner from "@/components/AdminBanner";
import LoginCard from "@/components/LoginCard";
import LoginPage from "@/app/admin/login/page";
import { AuthProvider } from "@/lib/auth";
import { P } from "@/lib/paths";
import { fakeApi, ok, fail } from "@/lib/testkit";
import type { AuthMe } from "@/lib/types";

const nav = vi.hoisted(() => ({ pushed: [] as string[] }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({
    push: (to: string) => { nav.pushed.push(to); },
    replace: () => {},
  }),
}));

const ADMIN: AuthMe = { email: "a@x.com", name: "阿管", role: "admin", kb_ids: null };
const MEMBER: AuthMe = { email: "m@x.com", name: "阿员", role: "member", kb_ids: [7] };

// 登录后 me 才可读的门闩式假后端：POST /auth/login 前 me=401，成功后 me=ok(me)
function loginApi(me: AuthMe) {
  let logged = false;
  const post = vi.fn(() => { logged = true; return ok(undefined); });
  const api = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? (logged ? ok(me) : fail("需要登录", 401)) : ok([])),
    POST: post,
  });
  return { api, post };
}

it("call captures HTTP status into ApiError; is401 matches 401 only", async () => {
  await expect(call(Promise.resolve({
    data: undefined, error: { detail: "需要登录" },
    response: new Response("x", { status: 401 }),
  }))).rejects.toMatchObject({ status: 401 });
  expect(is401(new ApiError({}, 401))).toBe(true);
  expect(is401(new ApiError({}, 500))).toBe(false);
  expect(is401(new Error("x"))).toBe(false);
});

it("AdminBanner turns 401 into login prompt, other errors stay plain", () => {
  const { unmount } = render(<AdminBanner error={new ApiError({ detail: "需要登录" }, 401)} />);
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("需要登录"); // 401 特化文案是 AdminBanner 固定串，不透传 detail（收编⑭：全员登录语义）
  expect(screen.getByRole("link", { name: "去登录" })).toHaveAttribute("href", "/admin/login");
  unmount();
  render(<AdminBanner error={new ApiError({ detail: "网关写库炸了" }, 500)} />);
  expect(screen.getByRole("alert")).toHaveTextContent("网关写库炸了");
  expect(screen.queryByRole("link", { name: "去登录" })).not.toBeInTheDocument();
});

describe("LoginCard", () => {
  it("提交邮箱+口令走 P.authLogin，成功后 onDone 收到 me", async () => {
    const { api, post } = loginApi(ADMIN);
    const done = vi.fn();
    render(<AuthProvider client={api}><LoginCard onDone={done} /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("邮箱"), "a@x.com");
    await userEvent.type(screen.getByLabelText("口令"), "pw-123");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(P.authLogin, expect.objectContaining({
      body: { email: "a@x.com", password: "pw-123" },
    })));
    expect(done).toHaveBeenCalledWith(ADMIN);
  });

  it("邮箱或口令错误：detail 上屏且不调 onDone", async () => {
    const api = fakeApi({
      GET: () => fail("需要登录", 401),
      POST: () => fail("邮箱或口令错误", 401),
    });
    const done = vi.fn();
    render(<AuthProvider client={api}><LoginCard onDone={done} /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("邮箱"), "a@x.com");
    await userEvent.type(screen.getByLabelText("口令"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("邮箱或口令错误");
    expect(done).not.toHaveBeenCalled();
  });

  it("限流 429：后端文案直接上屏", async () => {
    const api = fakeApi({
      GET: () => fail("需要登录", 401),
      POST: () => fail("失败次数过多，15 分钟后再试", 429),
    });
    render(<AuthProvider client={api}><LoginCard onDone={vi.fn()} /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("邮箱"), "a@x.com");
    await userEvent.type(screen.getByLabelText("口令"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("失败次数过多，15 分钟后再试");
  });
});

describe("登录页角色分流", () => {
  it("admin 登录成功跳 /admin/kb", async () => {
    nav.pushed = [];
    const { api } = loginApi(ADMIN);
    render(<AuthProvider client={api}><LoginPage /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("邮箱"), "a@x.com");
    await userEvent.type(screen.getByLabelText("口令"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(nav.pushed).toEqual(["/admin/kb"]));
  });

  it("member 登录成功跳聊天 /", async () => {
    nav.pushed = [];
    const { api } = loginApi(MEMBER);
    render(<AuthProvider client={api}><LoginPage /></AuthProvider>);
    await userEvent.type(screen.getByLabelText("邮箱"), "m@x.com");
    await userEvent.type(screen.getByLabelText("口令"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(nav.pushed).toEqual(["/"]));
  });
});

// P.authLogin 引用守护：登录链路只经 P 常量（URL 唯一事实源）
it("login path key matches P.authLogin", () => {
  expect(P.authLogin).toBe("/api/v1/auth/login");
});
