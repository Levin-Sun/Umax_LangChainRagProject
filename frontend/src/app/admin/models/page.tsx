"use client";
// 必须 use client：api 是含方法的对象，Server→Client 传参走 RSC 序列化会抛
// "Functions cannot be passed directly to Client Components"（任务 4/6 next build 实测）。
import ModelsAdmin from "@/components/ModelsAdmin";
import AdminGate from "@/components/AdminGate";
import { api } from "@/lib/api";

export default function Page() {
  return <AdminGate><ModelsAdmin api={api} /></AdminGate>;
}
