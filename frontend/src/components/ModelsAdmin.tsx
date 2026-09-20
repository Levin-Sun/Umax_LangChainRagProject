"use client";
// 模型后台：列表（key 掩码原样展示）/登记表单（scenario 下拉+必填校验）/启停/删除
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { ModelOut } from "@/lib/types";

// 与 backend/app/main.py 的 SCENARIOS 集合保持同步（契约经 pattern 入约，改集合走四步契约工作流）
export const SCENARIOS = ["chat", "embedding", "rerank", "vision"] as const;

const emptyForm = { scenario: "chat", provider: "", base_url: "", api_key: "", model_name: "", capabilities: {} as Record<string, unknown>, fallback_rank: 0, enabled: true, is_default: false };

export default function ModelsAdmin({ api }: { api: Client }) {
  const [form, setForm] = useState({ ...emptyForm });
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 任务7欠账②：行内操作（启停/删除）独立 busy 防双击竞态，错误经 ErrorBanner 呈现
  const [rowBusy, setRowBusy] = useState(false);
  const [rowErr, setRowErr] = useState<unknown>(null);
  const list = useAsync(() => call(api.GET(P.models)) as Promise<ModelOut[]>);

  async function register() {
    for (const k of ["provider", "base_url", "api_key", "model_name"] as const) {
      if (!String(form[k]).trim()) {
        setFormErr("厂商/地址/key/模型名均为必填");
        return;
      }
    }
    setFormErr(null);
    setBusy(true);
    try {
      await call(api.POST(P.models, { body: form }));
      setForm({ ...emptyForm });
      list.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(m: ModelOut) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await call(api.PATCH(P.model, { params: { path: { model_id: m.id } }, body: { enabled: !m.enabled } }));
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  async function remove(m: ModelOut) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await callVoid(api.DELETE(P.model, { params: { path: { model_id: m.id } } }));
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 px-6 py-6">
      <ErrorBanner error={list.error ?? rowErr} />
      <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-neutral-200 bg-neutral-50/60 text-left text-xs text-muted-foreground">
            <th className="px-4 py-2.5 font-medium">场景</th><th className="py-2.5 font-medium">模型</th>
            <th className="py-2.5 font-medium">API Key</th><th className="py-2.5 font-medium">fallback</th>
            <th className="py-2.5 pr-4 font-medium">操作</th></tr></thead>
          <tbody>
            {(list.data ?? []).map((m) => (
              <tr key={m.id} className="border-b border-neutral-100 last:border-0">
                <td className="px-4 py-2.5"><Badge variant="secondary" className="rounded-full font-normal">{m.scenario}</Badge></td>
                <td>{m.model_name}{m.is_default && <span className="ml-1 text-xs text-blue-600">默认</span>}
                  {!m.enabled && <span className="ml-1 text-xs text-muted-foreground">（停用）</span>}</td>
                <td className="font-mono text-xs">{m.api_key_masked}</td>
                <td className="text-muted-foreground">#{m.fallback_rank}</td>
                <td className="space-x-1.5 pr-4">
                  <button role="switch" aria-checked={m.enabled} aria-label={`启用 ${m.model_name}`}
                          className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                            m.enabled ? "border-neutral-300 text-neutral-600 hover:bg-neutral-50" : "border-neutral-200 text-muted-foreground"}`}
                          onClick={() => toggle(m)}>
                    {m.enabled ? "停用" : "启用"}
                  </button>
                  <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-red-500 hover:text-red-600" onClick={() => remove(m)}>删除</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="max-w-md space-y-2.5 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm"
            onSubmit={(e) => { e.preventDefault(); register(); }}>
        <h3 className="text-sm font-medium">登记模型</h3>
        <label className="block space-y-1">
          <span className="text-xs text-neutral-500">场景</span>
          <select className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-400"
                  value={form.scenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>
            {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        {([["provider", "厂商"], ["base_url", "接口地址"], ["api_key", "API 密钥"], ["model_name", "模型名"]] as const).map(([k, zh]) => (
          <label key={k} className="block space-y-1">
            <span className="text-xs text-neutral-500">{zh}</span>
            <Input placeholder={k} value={String(form[k])} type={k === "api_key" ? "password" : "text"}
                   className="h-9 rounded-lg border-neutral-300"
                   onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <label className="block space-y-1">
          <span className="text-xs text-neutral-500">回退优先级</span>
          <Input type="number" placeholder="fallback_rank" value={form.fallback_rank} className="h-9 rounded-lg border-neutral-300"
                 onChange={(e) => setForm({ ...form, fallback_rank: Number(e.target.value) || 0 })} />
        </label>
        {formErr && <p className="text-sm text-red-600">{formErr}</p>}
        <Button type="submit" disabled={busy} className="h-9 rounded-lg">{busy ? "提交中…" : "提交登记"}</Button>
      </form>
    </div>
  );
}
