"use client";
// admin 页门闸：读不到 admin_hint 标记就 replace 到登录页、不渲染 children。
// 这是体验层不是安全层——真实授权由后端 401 把关（AdminBanner 仍保留作兜底）。
// 测量与跳转必须在同一 effect 内：分开写会拿首帧的 hint=false 判定，
// 把「登录成功→push 到管理页」的正常流程也弹回登录页。
import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { isAdminHint } from "@/lib/api";

export function useAdminHint(): boolean {
  const path = usePathname();
  const [hint, setHint] = useState(false);
  useEffect(() => { setHint(isAdminHint()); }, [path]);
  return hint;
}

export default function AdminGate({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [hint, setHint] = useState(false);
  useEffect(() => {
    const h = isAdminHint();
    setHint(h);
    if (!h && path !== "/admin/login") router.replace("/admin/login");
  }, [path, router]);
  return hint || path === "/admin/login" ? <>{children}</> : null;
}
