"use client";
// 登录页（全员）：登录成功按角色分流——admin 进知识库后台，member 回聊天页。
// LoginCard 经 useAuth 取登录动作，无需再直传 api（RSC 序列化限制见任务 4 注释，此处为纯客户端组件）。
import { useRouter } from "next/navigation";
import LoginCard from "@/components/LoginCard";

export default function Page() {
  const router = useRouter();
  return (
    <div className="flex justify-center px-4 py-16">
      <LoginCard onDone={(m) => router.push(m.role === "admin" ? "/admin/kb" : "/")} />
    </div>
  );
}
