"use client";
import { Card } from "@/components/ui/card";
import AdminBanner from "@/components/AdminBanner";
import { call, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { UsageOut } from "@/lib/types";

export default function UsageAdmin({ api }: { api: Client }) {
  const sum = useAsync(() => call(api.GET(P.usageSummary)) as Promise<UsageOut[]>);
  const rows = sum.data ?? [];
  const totalCalls = rows.reduce((a, r) => a + r.calls, 0);
  const totalTokens = rows.reduce((a, r) => a + r.prompt_tokens + r.completion_tokens, 0);
  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 px-6 py-6">
      <AdminBanner error={sum.error} />
      <div className="flex gap-4">
        <Card className="flex-1 rounded-xl border-border p-4 shadow-sm">
          <p className="text-xs text-muted-foreground">总调用</p>
          <p className="mt-1 text-xl font-semibold">{totalCalls} 次</p>
        </Card>
        <Card className="flex-1 rounded-xl border-border p-4 shadow-sm">
          <p className="text-xs text-muted-foreground">总 tokens</p>
          <p className="mt-1 text-xl font-semibold">{totalTokens}</p>
        </Card>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-xs text-muted-foreground">
            <th className="px-4 py-2.5 font-medium">场景</th><th className="py-2.5 font-medium">模型</th>
            <th className="py-2.5 font-medium">调用</th><th className="py-2.5 font-medium">prompt</th>
            <th className="py-2.5 pr-4 font-medium">completion</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.scenario}:${r.model}`} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5">{r.scenario}</td><td>{r.model}</td>
                <td>{r.calls}</td><td className="text-muted-foreground">{r.prompt_tokens}</td>
                <td className="pr-4 text-muted-foreground">{r.completion_tokens}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
