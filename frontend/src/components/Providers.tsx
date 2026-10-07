"use client";
// 客户端壳：全站唯一的 Provider 挂载点（layout 是 async RSC，钩子须在 effect 里装）。
// setUnauthorizedHandler 只在此处注入：业务请求 401（会话过期/未登录）即整页换到登录页；
// 已在登录页时跳过——否则 /auth/me 的 401 会触发 assign 到本页，形成刷新死循环（brief 钩子的必要守卫）。
// 例外：显式 call/callVoid(..., {skipAuthRedirect:true}) 的请求不走这里（自助改密的
// "旧口令错误" 401 是表单校验错，把人踢去登录页会盖掉弹窗里的错误文案，终审收口③）。
// 首登强改密门闸：/auth/me 带 must_change_password 时全站罩强制改密框（后端 428 兜底，
// 改密成功 refresh 拉新 me 后自动消失）。
import { useEffect, type ReactNode } from "react";
import { api, setUnauthorizedHandler, type Client } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";

export function MustChangeGate({ client = api }: { client?: Client }) {
  const { me, refresh } = useAuth();
  if (!me?.must_change_password) return null;
  return <ChangePasswordDialog api={client} forced onClose={() => { void refresh(); }} />;
}

export function Providers({ children }: { children: ReactNode }) {
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (window.location.pathname !== "/admin/login") window.location.assign("/admin/login");
    });
  }, []);
  return (
    <AuthProvider>
      <MustChangeGate />
      {children}
    </AuthProvider>
  );
}
