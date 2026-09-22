"use client";
// 必须 use client：api 是含方法的对象，Server→Client 传参走 RSC 序列化会抛
// "Functions cannot be passed directly to Client Components"（任务 6 next build 实测补修）。
import KbAdmin from "@/components/KbAdmin";
import AdminGate from "@/components/AdminGate";
import { api } from "@/lib/api";

export default function Page() {
  return <AdminGate><KbAdmin api={api} /></AdminGate>;
}
