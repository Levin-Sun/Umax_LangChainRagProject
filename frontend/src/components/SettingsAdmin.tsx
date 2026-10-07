"use client";
// 配置中心（/admin/settings）：提示词与检索/切块参数后台可改，改完下一次提问即生效——
// 「一切差异进配置、不动代码」是这套系统可复制的生命线（§D）。
// 每字段独立「恢复默认」；保存只发改动过的字段（缺席=不动，null=回默认）。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { SettingsSnapshot } from "@/lib/types";

type Val = string | number | boolean;
const TEXT_KEYS = ["chat_system_prompt", "chat_miss_answer", "vision_prompt"];
// 表单顺序与控件形态：文本键给 textarea，数值键给 number 输入（min_sim 用小数步长）
const ROWS: [string, string][] = [
  ["chat_system_prompt", "多行"], ["chat_miss_answer", "单行"], ["vision_prompt", "多行"],
  ["doc_image_caption", "开关"],
  ["recall_k", "数字"], ["rerank_top_n", "数字"], ["min_sim", "数字"],
  ["chunk_target", "数字"], ["chunk_min", "数字"],
];

export default function SettingsAdmin({ api }: { api: Client }) {
  const snap = useAsync(() => call(api.GET(P.settings)) as Promise<SettingsSnapshot>);
  const [draft, setDraft] = useState<Record<string, Val>>({});
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const d = snap.data;
  const val = (k: string): Val => (k in draft ? draft[k] : (d?.values[k] ?? ""));
  const isOverridden = (k: string) => !!d?.overridden.includes(k);
  const dirty = Object.keys(draft).some((k) => String(draft[k]) !== String(d?.values[k]));

  function set(k: string, v: Val) {
    setSaved(false);
    setDraft((p) => ({ ...p, [k]: v }));
  }

  async function save() {
    if (busy || !dirty) return;
    setBusy(true);
    setErr(null);
    try {
      // 只提交真正改动过的键；恢复默认 = 显式 null（后端按 model_fields_set 区分「不动」与「回默认」）
      const body: Record<string, Val | null> = {};
      for (const [k, v] of Object.entries(draft)) {
        if (String(v) === String(d?.values[k])) continue;
        body[k] = v;
      }
      await callVoid(api.PUT(P.settings, { body }) as never);
      setDraft({});
      setSaved(true);
      snap.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function restore(key: string) {
    setBusy(true);
    setErr(null);
    try {
      await callVoid(api.PUT(P.settings, { body: { [key]: null } }) as never);
      setDraft((p) => { const n = { ...p }; delete n[key]; return n; });
      snap.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-6 py-6">
      <AdminBanner error={snap.error ?? err} />
      <p className="text-caption text-ink-3">
        改动即时生效（下一次提问/检索就用新值）；切块长度在建库时锁定，改后需对文档重处理。
      </p>
      {d?.warnings.map((w) => (
        <p key={w} className="rounded-lg border border-border bg-muted/60 px-4 py-2 text-body text-ink-2">
          ⚠️ {w}
        </p>
      ))}
      {ROWS.map(([k, kind]) => {
        const isText = TEXT_KEYS.includes(k);
        const isBool = kind === "开关";
        return (
          <div key={k} className="space-y-2 rounded-xl border border-border bg-card px-6 py-4 shadow-sm">
            <div className="flex items-center gap-2">
              <label htmlFor={k} className="text-h3 font-medium text-ink-2">
                {d?.labels[k] ?? k}
              </label>
              {isOverridden(k) && <span className="text-caption text-ink-3">已自定义</span>}
              {isOverridden(k) && (
                <Button size="sm" variant="ghost" className="ml-auto h-7 rounded-lg text-ink-3"
                        disabled={busy} onClick={() => void restore(k)}>恢复默认</Button>
              )}
            </div>
            {isBool ? (
              <label className="flex items-center gap-2 text-body text-ink-2">
                <input type="checkbox" aria-label={d?.labels[k] ?? k} disabled={busy}
                       checked={val(k) === true}
                       onChange={(e) => set(k, e.target.checked)} />
                {val(k) === true ? "已开启" : "已关闭"}
              </label>
            ) : isText ? (
              <textarea id={k} aria-label={d?.labels[k] ?? k} rows={kind === "多行" ? 4 : 2}
                        className="block w-full resize-y rounded-lg border border-border bg-transparent px-3 py-2 text-body text-ink-1 outline-none focus:border-ring"
                        value={String(val(k))} disabled={busy}
                        onChange={(e) => set(k, e.target.value)} />
            ) : (
              <Input id={k} aria-label={d?.labels[k] ?? k} type="number" inputMode="decimal"
                     step={k === "min_sim" ? "0.01" : "1"}
                     className="h-9 w-40 rounded-lg border-border" value={String(val(k))}
                     disabled={busy}
                     onChange={(e) => set(k, Number(e.target.value))} />
            )}
            {d?.help[k] && <p className="text-caption text-ink-3">{d.help[k]}</p>}
          </div>
        );
      })}
      <div className="flex items-center gap-3">
        <Button className="h-9 rounded-lg" disabled={busy || !dirty} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </Button>
        {saved && <span className="text-body text-ink-2">已保存。</span>}
      </div>
    </div>
  );
}
