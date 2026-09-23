"use client";
// 顶栏导航：登录态由 useAuth() 驱动（admin_hint 已退役）——admin 见全套管理链接，
// member/匿名只剩「聊天」；已登录右侧显 用户名+改密+退出。
// 退出 push("/") 回聊天页——但全员登录下聊天页对匿名即跳 /admin/login，故实际落登录页（收编⑬：原注释误导）。
// 改密成功后（ChangePasswordDialog.onChanged）logout 并跳登录页——新口令需重新登录。
import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { ThemeToggle } from "@/components/ThemeToggle";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";

const LINKS = [
  { href: "/", label: "聊天" },
  { href: "/admin/kb", label: "知识库" },
  { href: "/admin/models", label: "模型" },
  { href: "/admin/usage", label: "用量" },
  { href: "/admin/users", label: "用户" },
  { href: "/admin/audit", label: "审计" },
];

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  const { me, loaded, logout } = useAuth();
  const [pwOpen, setPwOpen] = useState(false);
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
                    onClick={() => setPwOpen(true)}>
              改密
            </button>
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
      {/* 条件挂载：只在打开时渲染，关闭即卸载——草稿口令（旧/新/确认）不留存；
          登录后的 me 恒在，触发按钮不引入额外拉取，也无 remount 循环 */}
      {pwOpen && me && (
        <ChangePasswordDialog api={api} onClose={() => setPwOpen(false)}
                              onChanged={async () => { await logout(); router.push("/admin/login"); }} />
      )}
    </nav>
  );
}
