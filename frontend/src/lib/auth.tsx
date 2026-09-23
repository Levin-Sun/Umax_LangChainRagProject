"use client";
// 全员登录基座：/auth/me 唯一真相源（admin_hint cookie 已退役）。挂载拉一次，登录/登出后 refresh。
// 401 静默降为匿名态（is401 吞掉），其余错误照抛给上层 ErrorBanner/控制台。
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api as defaultApi, call, callVoid, is401, type Client } from "./api";
import { P } from "./paths";
import type { AuthMe } from "./types";

type AuthState = {
  me: AuthMe | null; loaded: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<AuthMe>;
  logout: () => Promise<void>;
};
const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children, client = defaultApi }:
    { children: ReactNode; client?: Client }) {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loaded, setLoaded] = useState(false);

  const fetchMe = useCallback(async (): Promise<AuthMe | null> => {
    try { return (await call(client.GET(P.authMe))) as AuthMe; }
    catch (e) { if (is401(e)) return null; throw e; }
  }, [client]);

  const refresh = useCallback(async () => {
    // 收编⑫：/auth/me 非 401 失败（网关抖动/500）也必须解除骨架并降为匿名态，
    // 否则 loaded 永挂 false → 全站卡骨架，且 useEffect 的 void refresh() 抛未处理拒绝。
    try { setMe(await fetchMe()); }
    catch { setMe(null); }
    finally { setLoaded(true); }
  }, [fetchMe]);

  useEffect(() => { void refresh(); }, [refresh]);

  const login = useCallback(async (email: string, password: string): Promise<AuthMe> => {
    await callVoid(client.POST(P.authLogin, { body: { email, password } }));
    const next = await fetchMe();
    if (!next) throw new Error("登录后 me 为空");  // 204 成功后必须可读
    setMe(next); setLoaded(true);
    return next;
  }, [client, fetchMe]);

  const logout = useCallback(async () => {
    await callVoid(client.POST(P.authLogout, {} as never));
    setMe(null);
  }, [client]);

  return <Ctx.Provider value={{ me, loaded, refresh, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth 必须在 AuthProvider 内");
  return v;
}
