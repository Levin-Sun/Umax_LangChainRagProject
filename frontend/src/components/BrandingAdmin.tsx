"use client";
// 白标设置（/admin/branding）：品牌名 + logo（data:image base64 直存 app_settings，
// ≤400KB 前端先卡一刀给友好文案）。保存后 Nav/LoginCard 下次拉取即生效——"一套系统卖一家
// 像定做的一样"，一切差异进配置不动代码。
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { BrandingOut } from "@/lib/types";

const LOGO_MAX = 400 * 1024;   // 与后端 LOGO_MAX（base64 字符）对齐，按二进制近似卡
const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"];

export default function BrandingAdmin({ api }: { api: Client }) {
  const cur = useAsync(() => call(api.GET(P.branding)) as Promise<BrandingOut>);
  const [name, setName] = useState("");
  const [logo, setLogo] = useState<string | null>(null);      // null=未动；undefined 语义经 clearBtn
  const [clearLogo, setClearLogo] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const initRef = useRef(false);   // 仅首载同步一次：用户清空输入框不能被弹回当前值
  const dirty = (name && name !== cur.data?.brand_name) || logo !== null || clearLogo;

  if (cur.data && !initRef.current) {
    initRef.current = true;
    setName(cur.data.brand_name);
  }

  async function pickLogo(file: File) {
    setSaved(false);
    if (!LOGO_TYPES.includes(file.type)) { setFormErr("logo 仅支持 PNG/JPEG/WebP/GIF/SVG"); return; }
    if (file.size > LOGO_MAX) { setFormErr("logo 请控制在 400KB 以内"); return; }
    const dataUrl = await new Promise<string>((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result as string);
      r.onerror = () => rej(new Error("读取文件失败"));
      r.readAsDataURL(file);
    });
    setFormErr(null);
    setLogo(dataUrl);
    setClearLogo(false);
  }

  async function save() {
    if (busy || !dirty) return;
    setBusy(true);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {};
      if (name && name !== cur.data?.brand_name) body.brand_name = name;
      if (clearLogo) body.logo = null;
      else if (logo !== null) body.logo = logo;
      await callVoid(api.PUT(P.branding, { body }));
      setSaved(true);
      setLogo(null);
      setClearLogo(false);
      cur.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const shownLogo = clearLogo ? null : (logo ?? cur.data?.logo ?? null);
  return (
    <div className="mx-auto w-full max-w-2xl space-y-6 px-6 py-6">
      <AdminBanner error={cur.error ?? formErr} />
      <div className="space-y-4 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
        <h3 className="text-h2 font-semibold">品牌外观</h3>
        <p className="text-caption text-ink-3">改动即时生效于登录页与顶部导航——客户的牌子，不是我们的。</p>
        <div className="space-y-1">
          <span className="text-h3 font-medium text-ink-2">预览</span>
          <div className="flex h-12 items-center gap-3 rounded-lg border border-border px-4">
            {shownLogo
              ? <img src={shownLogo} alt="logo 预览" className="h-8 w-auto" />
              : <span className="text-ink-3">无 logo</span>}
            <span className="text-h3 font-semibold text-ink-1">{name || cur.data?.brand_name || "—"}</span>
          </div>
        </div>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">品牌名</span>
          <Input aria-label="品牌名" className="h-9 rounded-lg border-border" value={name}
                 onChange={(e) => { setName(e.target.value); setSaved(false); }} />
        </label>
        <div className="space-y-1">
          <span className="text-h3 font-medium text-ink-2">Logo</span>
          <div className="flex items-center gap-2">
            <input ref={fileRef} type="file" accept={LOGO_TYPES.join(",")} className="hidden"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void pickLogo(f); }} />
            <Button type="button" variant="outline" className="h-9 rounded-lg" disabled={busy}
                    onClick={() => fileRef.current?.click()}>选择图片…</Button>
            {shownLogo && (
              <Button type="button" variant="ghost" className="h-9 rounded-lg text-ink-3" disabled={busy}
                      onClick={() => { setClearLogo(true); setLogo(null); setSaved(false); }}>移除</Button>
            )}
          </div>
          <p className="text-caption text-ink-3">PNG/JPEG/WebP/GIF/SVG，≤400KB；存储在系统配置里，随库备份。</p>
        </div>
        {saved && <p className="text-body text-ink-2">已保存。</p>}
        <Button className="h-9 rounded-lg" disabled={busy || !dirty} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </Button>
      </div>
    </div>
  );
}
