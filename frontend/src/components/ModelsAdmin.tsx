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
    <div className="space-y-6 p-6">
      <ErrorBanner error={list.error ?? rowErr} />
      <table className="w-full text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">场景</th><th>模型</th><th>API Key</th><th>fallback</th><th>操作</th></tr></thead>
        <tbody>
          {(list.data ?? []).map((m) => (
            <tr key={m.id} className="border-b">
              <td className="py-1"><Badge variant="secondary">{m.scenario}</Badge></td>
              <td>{m.model_name}{m.is_default && <span className="ml-1 text-xs text-blue-600">默认</span>}
                {!m.enabled && <span className="ml-1 text-xs text-muted-foreground">（停用）</span>}</td>
              <td className="font-mono">{m.api_key_masked}</td>
              <td>#{m.fallback_rank}</td>
              <td className="space-x-2">
                <button role="switch" aria-checked={m.enabled} aria-label={`启用 ${m.model_name}`}
                        className="rounded border px-2 text-xs" onClick={() => toggle(m)}>
                  {m.enabled ? "停用" : "启用"}
                </button>
                <Button size="sm" variant="ghost" onClick={() => remove(m)}>删除</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="max-w-lg space-y-2 border-t pt-4" onSubmit={(e) => { e.preventDefault(); register(); }}>
        <h3 className="font-medium">登记模型</h3>
        <select aria-label="scenario" className="w-full rounded border p-2 text-sm"
                value={form.scenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>
          {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {(["provider", "base_url", "api_key", "model_name"] as const).map((k) => (
          <Input key={k} aria-label={k} placeholder={k} value={String(form[k])} type={k === "api_key" ? "password" : "text"}
                 onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
        ))}
        <Input aria-label="fallback_rank" type="number" value={form.fallback_rank}
               onChange={(e) => setForm({ ...form, fallback_rank: Number(e.target.value) || 0 })} />
        {formErr && <p className="text-sm text-red-600">{formErr}</p>}
        <Button type="submit" disabled={busy}>{busy ? "提交中…" : "提交登记"}</Button>
      </form>
    </div>
  );
}
