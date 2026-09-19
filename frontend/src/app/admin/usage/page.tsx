"use client";
// 必须 use client：api 是含方法的对象，Server→Client 传参走 RSC 序列化会抛
// "Functions cannot be passed directly to Client Components"（任务 4/6 next build 实测）。
import UsageAdmin from "@/components/UsageAdmin";
import { api } from "@/lib/api";

export default function Page() {
  return <UsageAdmin api={api} />;
}
