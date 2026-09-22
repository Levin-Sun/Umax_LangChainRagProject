import Link from "next/link";
import { ErrorBanner } from "@/components/ErrorBanner";
import { is401 } from "@/lib/api";

// 管理页横幅：401 特化为"去登录"入口，其余错误走普通 ErrorBanner
export default function AdminBanner({ error }: { error?: unknown }) {
  if (!error) return null;
  if (is401(error)) {
    return (
      <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        需要管理员登录 · <Link href="/admin/login" className="font-medium underline">去登录</Link>
      </div>
    );
  }
  return <ErrorBanner error={error} />;
}
