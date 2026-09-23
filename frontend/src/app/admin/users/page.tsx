"use client";
// 必须 use client：api 是含方法的对象，Server→Client 传参走 RSC 序列化会抛
// "Functions cannot be passed directly to Client Components"（任务 4/6 实测）。
// me 由 useAuth 取（/auth/me 真相源）：UsersAdmin 需它做"当前登录者行"自身保护。
import UsersAdmin from "@/components/UsersAdmin";
import AdminGate from "@/components/AdminGate";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

export default function Page() {
  const { me } = useAuth();
  return <AdminGate>{me ? <UsersAdmin api={api} me={me} /> : null}</AdminGate>;
}
