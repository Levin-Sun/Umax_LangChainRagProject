// 登录态的前端面：/auth/me 是唯一真相源（admin_hint cookie 已退役）——
// 未登录 Nav 只剩「聊天 + 登录」，admin 才有管理链接/用户名/退出，member 只见聊天与用户名；
// AdminGate 按 me 分流：loading 不渲染不跳（不闪），anon→登录页，member→聊天，admin→渲染 children。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Nav } from "@/components/Nav";
import AdminGate from "@/components/AdminGate";
import { AuthProvider } from "@/lib/auth";
import { P } from "@/lib/paths";
import { fakeApi, ok, fail } from "@/lib/testkit";
import type { AuthMe } from "@/lib/types";

const nav = vi.hoisted(() => ({ path: "/", pushed: [] as string[], replaced: [] as string[] }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => ({
    push: (to: string) => { nav.pushed.push(to); },
    replace: (to: string) => { nav.replaced.push(to); },
  }),
}));

const ADMIN: AuthMe = { email: "a@x.com", name: "阿管", role: "admin", kb_ids: null };
const MEMBER: AuthMe = { email: "m@x.com", name: "阿员", role: "member", kb_ids: [7] };

const authed = (me: AuthMe | null, post = vi.fn(() => ok(undefined))) =>
  fakeApi({ GET: (u) => (u.includes("/auth/me") ? (me ? ok(me) : fail("需要登录", 401)) : ok([])), POST: post });

function at(path: string) {
  nav.path = path;
  nav.pushed = [];
  nav.replaced = [];
}

describe("Nav", () => {
  it("未登录：只剩聊天 + 登录入口，无管理链接", async () => {
    at("/");
    render(<AuthProvider client={authed(null)}><Nav /></AuthProvider>);
    expect(await screen.findByRole("link", { name: "登录" })).toHaveAttribute("href", "/admin/login");
    expect(screen.getByRole("link", { name: "聊天" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "知识库" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出" })).not.toBeInTheDocument();
  });

  it("登录页自身不出现登录链接", async () => {
    at("/admin/login");
    render(<AuthProvider client={authed(null)}><Nav /></AuthProvider>);
    await screen.findByText("Umax RAG");
    expect(screen.queryByRole("link", { name: "登录" })).not.toBeInTheDocument();
  });

  it("admin：管理链接齐 + 用户名，退出清会话后跳聊天", async () => {
    const post = vi.fn(() => ok(undefined));
    at("/admin/kb");
    render(<AuthProvider client={authed(ADMIN, post)}><Nav /></AuthProvider>);
    expect(await screen.findByRole("link", { name: "知识库" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "模型" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "用量" })).toBeInTheDocument();
    expect(screen.getByText("阿管")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "退出" }));
    expect(post).toHaveBeenCalledWith(P.authLogout, expect.anything());
    await waitFor(() => expect(nav.pushed).toEqual(["/"]));
  });

  it("member：只有聊天与用户名，无管理链接也无登录入口", async () => {
    at("/");
    render(<AuthProvider client={authed(MEMBER)}><Nav /></AuthProvider>);
    expect(await screen.findByText("阿员")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "聊天" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "知识库" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "登录" })).not.toBeInTheDocument();
  });
});

describe("AdminGate", () => {
  it("me 未返回（loading）：不渲染 children 也不跳转（不闪）", () => {
    at("/admin/models");
    const pending = { GET: () => new Promise(() => {}), POST: () => ok(undefined) } as never;
    render(<AuthProvider client={pending}><AdminGate><p>管理内容</p></AdminGate></AuthProvider>);
    expect(screen.queryByText("管理内容")).not.toBeInTheDocument();
    expect(nav.replaced).toEqual([]);
  });

  it("admin：渲染 children，不跳转", async () => {
    at("/admin/models");
    render(<AuthProvider client={authed(ADMIN)}><AdminGate><p>管理内容</p></AdminGate></AuthProvider>);
    expect(await screen.findByText("管理内容")).toBeInTheDocument();
    expect(nav.replaced).toEqual([]);
  });

  it("member 访问 admin：弹回聊天且不渲染内容", async () => {
    at("/admin/users");
    render(<AuthProvider client={authed(MEMBER)}><AdminGate><p>管理内容</p></AdminGate></AuthProvider>);
    await waitFor(() => expect(nav.replaced).toEqual(["/"]));
    expect(screen.queryByText("管理内容")).not.toBeInTheDocument();
  });

  it("未登录访问 admin：弹回登录页", async () => {
    at("/admin/kb");
    render(<AuthProvider client={authed(null)}><AdminGate><p>管理内容</p></AdminGate></AuthProvider>);
    await waitFor(() => expect(nav.replaced).toEqual(["/admin/login"]));
    expect(screen.queryByText("管理内容")).not.toBeInTheDocument();
  });
});
