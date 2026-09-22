// 登录标记的前端面：isAdminHint 只读 JS 可见的 admin_hint cookie；
// Nav 未登录只剩「聊天 + 管理员登录」，登录后才出现知识库/模型/用量与退出；
// AdminGate 在无标记时把 admin 页内容替换为跳转（授权兜底仍是后端 401）。
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { isAdminHint } from "@/lib/api";
import { Nav } from "@/components/Nav";
import AdminGate from "@/components/AdminGate";

const nav = vi.hoisted(() => ({ path: "/", replaced: [] as string[] }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => ({ replace: (to: string) => { nav.replaced.push(to); } }),
}));

function at(path: string, hint: boolean) {
  nav.path = path;
  nav.replaced = [];
  // 空值即删除（RFC 6265 语义，jsdom 一致）——比残留 admin_hint= 更贴近真实清 cookie
  document.cookie = "admin_hint=" + (hint ? "1" : "") + "; path=/";
}

it("isAdminHint reflects the admin_hint cookie", () => {
  at("/", false);
  expect(isAdminHint()).toBe(false);
  at("/", true);
  expect(isAdminHint()).toBe(true);
});

it("Nav without hint shows only chat plus a login entry", () => {
  at("/", false);
  render(<Nav />);
  expect(screen.getByRole("link", { name: "聊天" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "知识库" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "模型" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "用量" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "管理员登录" })).toHaveAttribute("href", "/admin/login");
});

it("Nav with hint shows admin links and logout", () => {
  at("/admin/kb", true);
  render(<Nav />);
  expect(screen.getByRole("link", { name: "知识库" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "模型" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "用量" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "退出" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "管理员登录" })).not.toBeInTheDocument();
});

it("AdminGate renders children when hinted", () => {
  at("/admin/models", true);
  render(<AdminGate><p>管理内容</p></AdminGate>);
  expect(screen.getByText("管理内容")).toBeInTheDocument();
  expect(nav.replaced).toEqual([]);
});

it("AdminGate without hint hides children and redirects to login", () => {
  at("/admin/models", false);
  render(<AdminGate><p>管理内容</p></AdminGate>);
  expect(screen.queryByText("管理内容")).not.toBeInTheDocument();
  expect(nav.replaced).toEqual(["/admin/login"]);
});

it("AdminGate lets the login page through without a hint", () => {
  at("/admin/login", false);
  render(<AdminGate><p>登录卡</p></AdminGate>);
  expect(screen.getByText("登录卡")).toBeInTheDocument();
  expect(nav.replaced).toEqual([]);
});
