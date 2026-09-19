"use client";
import { Card } from "@/components/ui/card";
import { ErrorBanner } from "@/components/ErrorBanner";
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
    <div className="space-y-6 p-6">
      <ErrorBanner error={sum.error} />
      <div className="flex gap-4">
        <Card className="flex-1 p-4"><p className="text-sm text-muted-foreground">总调用</p><p className="text-2xl font-semibold">{totalCalls} 次</p></Card>
        <Card className="flex-1 p-4"><p className="text-sm text-muted-foreground">总 tokens</p><p className="text-2xl font-semibold">{totalTokens}</p></Card>
      </div>
      <table className="w-full text-sm">
        <thead><tr className="border-b text-left"><th className="py-1">场景</th><th>模型</th><th>调用</th><th>prompt</th><th>completion</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.scenario}:${r.model}`} className="border-b">
              <td className="py-1">{r.scenario}</td><td>{r.model}</td><td>{r.calls}</td><td>{r.prompt_tokens}</td><td>{r.completion_tokens}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
