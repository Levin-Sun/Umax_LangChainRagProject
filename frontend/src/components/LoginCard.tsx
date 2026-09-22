"use client";
// 管理员登录卡：口令 POST /admin/login（成功由后端下发 HttpOnly cookie），onDone 由页面注入跳转
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorBanner } from "@/components/ErrorBanner";
import { callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";

export default function LoginCard({ api, onDone }: { api: Client; onDone: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await callVoid(api.POST(P.adminLogin, { body: { token } }));
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm"
          onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <h2 className="text-h2 font-semibold">管理员登录</h2>
      <p className="text-caption text-ink-3">知识库/模型/用量管理需要管理员口令；未配置 ADMIN_TOKEN 时管理端点保持开放。</p>
      <ErrorBanner error={error} />
      <Input aria-label="管理员口令" type="password" placeholder="管理员口令" value={token}
             className="h-9 rounded-lg border-border"
             onChange={(e) => setToken(e.target.value)} />
      <Button type="submit" disabled={!token.trim() || busy} className="h-9 w-full rounded-lg">
        {busy ? "登录中…" : "登录"}
      </Button>
    </form>
  );
}
