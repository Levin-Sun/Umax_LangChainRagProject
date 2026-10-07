"use client";
// API key 管理（/admin/apikeys）：开放 API 的密钥台账——列表（key 只显打码前缀）+ 新建
// （作用域=全库/指定库 + 月配额）+ 行内启停/删除。明文 key 只在创建成功弹窗里出现一次
// （后端契约如此），复制后关弹窗即再不可见——丢了只能删了重建。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { ApiKeyCreated, ApiKeyOut, KbOut } from "@/lib/types";

const emptyForm = { name: "", allKbs: true, quota: "" };

export default function ApiKeysAdmin({ api }: { api: Client }) {
  const [form, setForm] = useState({ ...emptyForm });
  const [formKbs, setFormKbs] = useState<number[]>([]);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null); // 带明文 key 的一次性弹窗
  const [copied, setCopied] = useState(false);
  const list = useAsync(() => call(api.GET(P.apiKeys)) as Promise<ApiKeyOut[]>);
  const kbs = useAsync(() => call(api.GET(P.kb)) as Promise<KbOut[]>);
  const rows = list.data ?? [];
  const kbRows = kbs.data ?? [];

  async function create() {
    if (!form.name.trim()) { setFormErr("名称必填"); return; }
    if (!form.allKbs && formKbs.length === 0) { setFormErr("指定库模式下至少勾选一个知识库"); return; }
    setFormErr(null);
    setBusy(true);
    try {
      const out = await call(api.POST(P.apiKeys, {
        body: {
          name: form.name,
          kb_ids: form.allKbs ? null : formKbs,
          monthly_token_quota: form.quota.trim() ? Number(form.quota) : null,
        },
      })) as ApiKeyCreated;
      setCreated(out);
      setCopied(false);
      setForm({ ...emptyForm });
      setFormKbs([]);
      list.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function patch(k: ApiKeyOut, body: Record<string, unknown>) {
    if (rowBusy) return;
    setRowBusy(true);
    try {
      await call(api.PATCH(P.apiKey, { params: { path: { key_id: k.id } }, body }));
      list.reload();
    } finally {
      setRowBusy(false);
    }
  }

  async function del(k: ApiKeyOut) {
    if (rowBusy) return;
    setRowBusy(true);
    try {
      await callVoid(api.DELETE(P.apiKey, { params: { path: { key_id: k.id } } }));
      list.reload();
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={list.error ?? kbs.error ?? formErr} />
      <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
        <table className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
            <th className="px-4 py-2.5 font-medium">名称</th><th className="py-2.5 font-medium">Key</th>
            <th className="py-2.5 font-medium">作用域</th><th className="py-2.5 font-medium">月配额</th>
            <th className="py-2.5 font-medium">状态</th><th className="py-2.5 font-medium">最近使用</th>
            <th className="py-2.5 pr-4 font-medium">操作</th></tr></thead>
          <tbody>
            {rows.map((k) => (
              <tr key={k.id} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5">{k.name}</td>
                <td className="font-mono">{k.key_prefix}…</td>
                <td className="font-medium">{k.kb_ids === null ? "全部" : k.kb_ids.join(", ") || "—"}</td>
                <td>{k.monthly_token_quota === null ? "不限" : k.monthly_token_quota.toLocaleString()}</td>
                <td><Badge variant={k.enabled ? "secondary" : "destructive"} className="rounded-full font-normal">
                  {k.enabled ? "启用" : "已停用"}</Badge></td>
                <td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—"}</td>
                <td className="space-x-1.5 pr-4">
                  {k.enabled
                    ? <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive hover:text-destructive"
                              disabled={rowBusy} onClick={() => patch(k, { enabled: false })}>停用</Button>
                    : <Button size="sm" variant="ghost" className="h-7 rounded-lg" disabled={rowBusy}
                              onClick={() => patch(k, { enabled: true })}>启用</Button>}
                  <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive hover:text-destructive"
                          disabled={rowBusy} onClick={() => del(k)}>删除</Button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-ink-3">还没有 API key</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <form className="max-w-md space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm"
            onSubmit={(e) => { e.preventDefault(); create(); }}>
        <h3 className="text-h2 font-semibold">新建 API key</h3>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">名称</span>
          <Input aria-label="名称" placeholder="如：钉钉机器人" className="h-9 rounded-lg border-border"
                 value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <div className="space-y-1">
          <span className="text-h3 font-medium text-ink-2">作用域</span>
          <label className="flex items-center gap-2 text-body text-ink-2">
            <input type="checkbox" aria-label="全库" checked={form.allKbs} disabled={busy}
                   onChange={(e) => setForm({ ...form, allKbs: e.target.checked })} />
            全部知识库
          </label>
          {!form.allKbs && (
            <div className="ml-5 space-y-1.5">
              {kbRows.map((k) => (
                <label key={k.id} className="flex items-center gap-2 text-body text-ink-2">
                  <input type="checkbox" aria-label={`库 ${k.name}`} checked={formKbs.includes(k.id)} disabled={busy}
                         onChange={(e) => setFormKbs((prev) =>
                           e.target.checked ? [...prev, k.id] : prev.filter((id) => id !== k.id))} />
                  {k.name}
                </label>
              ))}
            </div>
          )}
        </div>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">月配额（tokens，留空不限）</span>
          <Input aria-label="月配额" inputMode="numeric" placeholder="如 1000000" className="h-9 rounded-lg border-border"
                 value={form.quota} onChange={(e) => setForm({ ...form, quota: e.target.value })} />
        </label>
        <Button type="submit" disabled={busy} className="h-9 rounded-lg">{busy ? "提交中…" : "新建 API key"}</Button>
      </form>
      {created && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div role="dialog" aria-label="API key 已创建"
               className="w-full max-w-md space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">API key 已创建</h3>
            <p className="text-caption text-ink-3">
              明文只在这一次显示，关闭后不再可见（丢了只能删除重建）。请立即复制并妥善保管。
            </p>
            <code className="block break-all rounded-lg border border-border bg-muted/60 px-3 py-2 font-mono text-body text-ink-1">
              {created.key}
            </code>
            <p className="text-caption text-ink-3">
              客户端接入：OpenAI SDK 把 base_url 设为 <code className="font-mono">http://&lt;host&gt;:8000/api/v1/openai</code>，
              API key 填这枚明文即可（/chat/completions 兼容端点，回答自带 citations 引用字段）。
            </p>
            <div className="flex gap-2">
              <Button className="h-9 rounded-lg" disabled={copied}
                      onClick={async () => {
                        await navigator.clipboard?.writeText(created.key);
                        setCopied(true);
                      }}>{copied ? "已复制" : "复制"}</Button>
              <Button variant="ghost" className="h-9 rounded-lg text-ink-3" onClick={() => setCreated(null)}>我已保存，关闭</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
