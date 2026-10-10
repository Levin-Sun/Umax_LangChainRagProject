"use client";
import { Card } from "@/components/ui/card";
import AdminBanner from "@/components/AdminBanner";
import { call, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import type { UsageOut, UsageUserOut } from "@/lib/types";

export default function UsageAdmin({ api }: { api: Client }) {
  const sum = useAsync(() => call(api.GET(P.usageSummary)) as Promise<UsageOut[]>);
  // 按人视图（§C 配额）：谁在用、谁快到线——管理成本的事实来源
  const byUser = useAsync(() => call(api.GET(P.usageUsers)) as Promise<UsageUserOut[]>);
  const rows = sum.data ?? [];
  const users = byUser.data ?? [];
  const pct = (used: number, limit: number | null) =>
    limit === null ? null : Math.min(100, Math.round((used / limit) * 100));
  const totalCalls = rows.reduce((a, r) => a + r.calls, 0);
  const totalTokens = rows.reduce((a, r) => a + r.prompt_tokens + r.completion_tokens, 0);
  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={sum.error ?? byUser.error} />
      <div className="flex gap-6">
        <Card className="flex-1 rounded-xl border-border px-6 py-5 shadow-sm">
          <p className="text-caption text-ink-3">总调用</p>
          <p className="mt-3 text-h2 font-semibold">{totalCalls} 次</p>
        </Card>
        <Card className="flex-1 rounded-xl border-border px-6 py-5 shadow-sm">
          <p className="text-caption text-ink-3">总 tokens</p>
          <p className="mt-3 text-h2 font-semibold">{totalTokens}</p>
        </Card>
      </div>
      <div className="space-y-2">
        <h3 className="text-h2 font-semibold">按人用量（今日 / 本月）</h3>
        <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
          <table className="w-full text-body text-ink-2">
            <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
              <th className="px-4 py-2.5 font-medium">用户</th>
              <th className="py-2.5 font-medium">今日</th><th className="py-2.5 font-medium">本月</th>
              <th className="py-2.5 font-medium">日/月上限</th><th className="py-2.5 pr-4 font-medium">状态</th></tr></thead>
            <tbody>
              {users.map((u) => {
                const dPct = pct(u.daily_used, u.daily_limit);
                const mPct = pct(u.monthly_used, u.monthly_limit);
                return (
                  <tr key={u.id} className="border-b border-border last:border-0">
                    <td className="px-4 py-2.5">{u.name}<span className="ml-2 text-caption text-ink-3">{u.email}</span></td>
                    <td className="font-medium">{u.daily_used}{dPct !== null && <span className="ml-1 text-caption text-ink-3">({dPct}%)</span>}</td>
                    <td className="font-medium">{u.monthly_used}{mPct !== null && <span className="ml-1 text-caption text-ink-3">({mPct}%)</span>}</td>
                    <td>{u.daily_limit ?? "不限"} / {u.monthly_limit ?? "不限"}</td>
                    <td className="pr-4">
                      {u.exceeded
                        ? <Badge variant="destructive" className="rounded-full font-normal">已超限</Badge>
                        : u.near_limit
                          ? <Badge variant="secondary" className="rounded-full font-normal">接近上限</Badge>
                          : <span className="text-ink-3">正常</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <h3 className="text-h2 font-semibold">按场景 / 模型</h3>
      <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
        <table className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
            <th className="px-4 py-2.5 font-medium">场景</th><th className="py-2.5 font-medium">模型</th>
            <th className="py-2.5 font-medium">调用</th><th className="py-2.5 font-medium">prompt</th>
            <th className="py-2.5 font-medium">completion</th>
            <th className="py-2.5 pr-4 font-medium">平均耗时</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.scenario}:${r.model}`} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5">{r.scenario}</td><td>{r.model}</td>
                <td className="font-medium">{r.calls}</td><td className="font-medium">{r.prompt_tokens}</td>
                <td className="font-medium">{r.completion_tokens}</td>
                <td className="pr-4 font-medium">{r.avg_latency_ms === null ? "—" : `${r.avg_latency_ms} ms`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
