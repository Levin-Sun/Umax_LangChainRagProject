"use client";
// 审计查询（/admin/audit，只读）：用户邮箱 Input + 动作 Select 过滤 + offset 步进 50 分页。
// AUDIT_ACTIONS 是 spec §5 事件表的前端镜像——与 backend/app/services/audit.py ACTIONS 集合同步，
// 加动作必须先进 spec 事件表再补这里（四步契约工作流的 UI 侧）。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import AdminBanner from "@/components/AdminBanner";
import { call, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { AuditOut } from "@/lib/types";

export const AUDIT_ACTIONS = [
  "login_success", "login_failed", "logout",
  "user_created", "user_updated", "grants_updated",
  "kb_created", "kb_deleted", "document_uploaded", "document_deleted",
  "document_reprocessed", "model_created", "model_updated", "model_deleted",
] as const;

const PAGE = 50; // 与后端默认 limit 对齐；上一页/下一页按此步进

export default function AuditAdmin({ api }: { api: Client }) {
  const [userInput, setUserInput] = useState("");
  const [actionInput, setActionInput] = useState("");
  // applied=已提交查询条件（翻页保持）；offset 步进；limit 固定 PAGE
  const [applied, setApplied] = useState({ user: "", action: "", offset: 0 });
  // 收编⑲：tick 保证「条件未变仍在 offset 0 点查询」也强制重取（否则 useAsync 依赖不变 → 查询按钮读起来像坏了）
  const [tick, setTick] = useState(0);
  const q = useAsync(
    () => call(api.GET(P.audit, {
      params: { query: {
        user: applied.user || undefined, action: applied.action || undefined,
        limit: PAGE, offset: applied.offset,
      } },
    })) as Promise<AuditOut[]>,
    [applied.user, applied.action, applied.offset, tick]);
  const rows = q.data ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={q.error} />
      <form className="flex flex-wrap items-end gap-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm"
            onSubmit={(e) => { e.preventDefault(); setApplied({ user: userInput.trim(), action: actionInput, offset: 0 }); setTick((t) => t + 1); }}>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">用户</span>
          <Input placeholder="邮箱" value={userInput} className="h-9 w-56 rounded-lg border-border"
                 onChange={(e) => setUserInput(e.target.value)} />
        </label>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">动作</span>
          <select className="h-9 rounded-lg border border-border px-3 text-body text-ink-1 outline-none focus:border-ring"
                  value={actionInput} onChange={(e) => setActionInput(e.target.value)}>
            <option value="">全部动作</option>
            {AUDIT_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <Button type="submit" className="h-9 rounded-lg">查询</Button>
      </form>
      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <table className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
            <th className="px-4 py-2.5 font-medium">时间</th><th className="py-2.5 font-medium">用户</th>
            <th className="py-2.5 font-medium">动作</th><th className="py-2.5 font-medium">对象</th>
            <th className="py-2.5 font-medium">IP</th><th className="py-2.5 pr-4 font-medium">详情</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.user_email ?? "—"}</td>
                <td>{r.action}</td>
                <td>{r.target_type ? `${r.target_type}#${r.target_id ?? "—"}` : "—"}</td>
                <td className="font-mono text-caption">{r.ip ?? "—"}</td>
                <td className="max-w-64 truncate pr-4 text-caption text-ink-3" title={JSON.stringify(r.detail)}>
                  {JSON.stringify(r.detail)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!q.loading && rows.length === 0 && (
          <p className="px-6 py-5 text-caption text-ink-3">没有匹配的审计记录</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <Button size="sm" variant="outline" className="h-8 rounded-lg" disabled={applied.offset === 0 || q.loading}
                onClick={() => setApplied((a) => ({ ...a, offset: Math.max(0, a.offset - PAGE) }))}>
          上一页
        </Button>
        <span className="text-caption text-ink-3">第 {applied.offset / PAGE + 1} 页 · 每页 {PAGE} 条</span>
        <Button size="sm" variant="outline" className="h-8 rounded-lg" disabled={q.loading}
                onClick={() => setApplied((a) => ({ ...a, offset: a.offset + PAGE }))}>
          下一页
        </Button>
      </div>
    </div>
  );
}
