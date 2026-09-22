"use client";
// 同 admin 其他页：api 对象须经 use client 页直传组件（RSC 序列化限制，见 models/page.tsx 注释）
import { useRouter } from "next/navigation";
import LoginCard from "@/components/LoginCard";
import { api } from "@/lib/api";

export default function Page() {
  const router = useRouter();
  return (
    <div className="flex justify-center px-4 py-16">
      <LoginCard api={api} onDone={() => router.push("/admin/models")} />
    </div>
  );
}
