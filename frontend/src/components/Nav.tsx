"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api, logout } from "@/lib/api";
import { useAdminHint } from "@/components/AdminGate";
import { ThemeToggle } from "@/components/ThemeToggle";

const LINKS = [
  { href: "/", label: "聊天" },
  { href: "/admin/kb", label: "知识库" },
  { href: "/admin/models", label: "模型" },
  { href: "/admin/usage", label: "用量" },
];

export function Nav() {
  const path = usePathname();
  const hint = useAdminHint();
  const links = hint ? LINKS : LINKS.slice(0, 1);
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
        {!hint && path !== "/admin/login" && (
          <Link href="/admin/login"
                className="rounded-lg px-3 py-1.5 text-body text-muted-foreground transition-colors hover:bg-accent">
            管理员登录
          </Link>
        )}
        {hint && path.startsWith("/admin") && (
          <button className="rounded-lg px-3 py-1.5 text-body text-muted-foreground transition-colors hover:bg-accent"
                  onClick={() => logout(api, () => { window.location.href = "/admin/login"; })}>
            退出
          </button>
        )}
      </div>
    </nav>
  );
}
