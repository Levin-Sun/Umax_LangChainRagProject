"use client";
// 登录卡（全员）：邮箱+口令 POST /auth/login（成功由后端下发 HttpOnly umax_session cookie），
// 经 useAuth().login 重拉 /auth/me 后 onDone(me)——页面按角色分流跳转。
// 失败（含 429 限流文案）直接喂 ErrorBanner 拆包上屏。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorBanner } from "@/components/ErrorBanner";
import { useAuth } from "@/lib/auth";
import { api, call } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { AuthMe, BrandingOut } from "@/lib/types";

export default function LoginCard({ onDone }: { onDone: (me: AuthMe) => void }) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // 白标：登录页首屏显客户品牌（GET /branding 匿名可读；失败回落默认不挡登录）
  const branding = useAsync(() => call(api.GET(P.branding)) as Promise<BrandingOut>);

  async function submit() {
    const e = email.trim();
    if (!e || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await login(e, password));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm"
          onSubmit={(ev) => { ev.preventDefault(); submit(); }}>
      {branding.data?.logo && <img src={branding.data.logo} alt="" className="h-10 w-auto" />}
      <h2 className="text-h2 font-semibold">{branding.data?.brand_name ?? "登录"}</h2>
      <p className="text-caption text-ink-3">知识库问答与后台管理均需账号登录。</p>
      <ErrorBanner error={error} />
      <Input aria-label="邮箱" type="email" placeholder="邮箱" value={email}
             className="h-9 rounded-lg border-border"
             onChange={(ev) => setEmail(ev.target.value)} />
      <Input aria-label="口令" type="password" placeholder="口令" value={password}
             className="h-9 rounded-lg border-border"
             onChange={(ev) => setPassword(ev.target.value)} />
      <Button type="submit" disabled={!email.trim() || !password || busy} className="h-9 w-full rounded-lg">
        {busy ? "登录中…" : "登录"}
      </Button>
    </form>
  );
}
