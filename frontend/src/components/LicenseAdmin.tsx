"use client";
// 授权状态页（/admin/license）：客户自助看到期时间/绑定机器/功能集，并复制机器指纹去申请续期。
// 到期时本页仍可访问（只读不受门闸影响），写操作才被后端 403 拦下——所以这里是排障第一站。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import AdminBanner from "@/components/AdminBanner";
import { call, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { LicenseOut } from "@/lib/types";

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

export default function LicenseAdmin({ api }: { api: Client }) {
  const st = useAsync(() => call(api.GET(P.license)) as Promise<LicenseOut>);
  const [copied, setCopied] = useState(false);
  const d = st.data;

  async function copyFingerprint() {
    await navigator.clipboard?.writeText(d?.machine_fingerprint ?? "");
    setCopied(true);
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-6 py-6">
      <AdminBanner error={st.error} />
      {d && (
        <>
          <div className="space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
            <div className="flex items-center gap-3">
              <h3 className="text-h2 font-semibold">授权状态</h3>
              <Badge variant={d.valid ? "secondary" : "destructive"} className="rounded-full font-normal">
                {!d.enforced ? "开发模式" : d.valid ? "有效" : "不可用"}
              </Badge>
              {d.valid && d.days_left !== null && d.days_left <= 30 && (
                <span className="text-caption text-destructive">即将到期，请及时续期</span>
              )}
            </div>
            {d.reason && <p className="text-body text-ink-2">{d.reason}</p>}
            {!d.valid && d.enforced && (
              <p className="text-caption text-ink-3">
                授权不可用期间系统为只读：问答与查看照常，建库/上传/改模型等管理操作会被拒绝。
              </p>
            )}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-body">
              {([["客户", d.customer ?? "—"], ["授权编号", d.license_key ?? "—"],
                 ["签发时间", fmt(d.issued_at)], ["到期时间", fmt(d.expires_at)],
                 ["剩余天数", d.days_left === null ? "—" : `${d.days_left} 天`],
                 ["功能集", Object.keys(d.features).length ? JSON.stringify(d.features) : "—"],
               ] as const).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="shrink-0 text-ink-3">{k}</dt>
                  <dd className="text-ink-1">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
            <h3 className="text-h2 font-semibold">机器指纹</h3>
            <p className="text-caption text-ink-3">
              申请授权或续期时把下面这串发给服务商（授权文件与本机绑定，换了服务器需要重新签发）。
            </p>
            <code className="block break-all rounded-lg border border-border bg-muted/60 px-3 py-2 font-mono text-body text-ink-1">
              {d.machine_fingerprint}
            </code>
            <Button variant="outline" className="h-9 rounded-lg" disabled={copied}
                    onClick={() => void copyFingerprint()}>
              {copied ? "已复制" : "复制指纹"}
            </Button>
          </div>
        </>
      )}
      {!d && !st.error && <p className="text-caption text-ink-3">加载中…</p>}
    </div>
  );
}
