"use client";
// admin 页门闸：/auth/me 为真相源——未登录弹回登录页，member 弹回聊天。
// 体验层非安全层；真授权由后端 401/403 把关（AdminBanner 兜底保留）。
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function AdminGate({ children }: { children: ReactNode }) {
  const { me, loaded } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loaded) return;
    if (!me) router.replace("/admin/login");
    else if (me.role !== "admin") router.replace("/");
  }, [loaded, me, router]);
  return me && me.role === "admin" ? <>{children}</> : null;
}
