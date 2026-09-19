"use client";
// 必须 use client：api 是含方法的对象，Server→Client 传参走 RSC 序列化会抛
// "Functions cannot be passed directly to Client Components"（任务 4 next build 实测）。
// 纯 CSR 挂载后 api 单例仅在浏览器构造，相对 baseUrl "" 同源直连（Task 3 Ruling-minor 一致）。
import ChatApp from "@/components/ChatApp";
import { api } from "@/lib/api";

export default function Home() {
  return <ChatApp api={api} />;
}
