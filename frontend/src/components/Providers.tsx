"use client";
// 客户端壳：全站唯一的 Provider 挂载点（layout 是 async RSC，钩子须在 effect 里装）。
// setUnauthorizedHandler 只在此处注入：任意业务请求 401（会话过期/未登录）即整页换到登录页；
// 已在登录页时跳过——否则 /auth/me 的 401 会触发 assign 到本页，形成刷新死循环（brief 钩子的必要守卫）。
import { useEffect, type ReactNode } from "react";
import { setUnauthorizedHandler } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";

export function Providers({ children }: { children: ReactNode }) {
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (window.location.pathname !== "/admin/login") window.location.assign("/admin/login");
    });
  }, []);
  return <AuthProvider>{children}</AuthProvider>;
}
