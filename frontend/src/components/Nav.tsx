"use client";
// 顶栏导航：登录态由 useAuth() 驱动（admin_hint 已退役）——admin 见全套管理链接，
// member/匿名只剩「聊天」；已登录右侧显 用户名+退出（退出后回聊天页）。
// 「改密」入口随 Task 8 的 ChangePasswordDialog 一起接入（此处不放死按钮）。
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "@/components/ThemeToggle";

const LINKS = [
  { href: "/", label: "聊天" },
  { href: "/admin/kb", label: "知识库" },
  { href: "/admin/models", label: "模型" },
  { href: "/admin/usage", label: "用量" },
];

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  const { me, loaded, logout } = useAuth();
  const links = me?.role === "admin" ? LINKS : LINKS.slice(0, 1);

  async function onLogout() {
    try { await logout(); } finally { router.push("/"); }
  }

  return (
    <nav className="flex h-12 items-center gap-1 border-b border-border/80 px-4">
      <span className="mr-3 text-h3 font-semibold">Umax RAG</span>
      {links.map(({ href, label }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href}
                className={`rounded-lg px-3 py-1.5 text-body transition-colors ${
                  active ? "bg-brand-soft font-medium text-ink-1" : "text-muted-foreground hover:bg-accent"}`}>
            {label}
          </Link>
        );
      })}
      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        {me && (
          <>
            <span className="px-3 py-1.5 text-body text-ink-3">{me.name}</span>
            <button className="rounded-lg px-3 py-1.5 text-body text-muted-foreground transition-colors hover:bg-accent"
                    onClick={() => void onLogout()}>
              退出
            </button>
          </>
        )}
        {!me && loaded && path !== "/admin/login" && (
          <Link href="/admin/login"
                className="rounded-lg px-3 py-1.5 text-body text-muted-foreground transition-colors hover:bg-accent">
            登录
          </Link>
        )}
      </div>
    </nav>
  );
}
