"use client";
// 知识库后台：kb 列表/新建（无删除端点，不做）→ 选中后文档表轮询 + 上传 + reprocess + chunks 抽屉
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, uploadDocument, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync, usePolling } from "@/lib/hooks";
import type { ChunkOut, DocOut, KbOut } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  pending: "排队中", parsing: "解析中", ready: "就绪", failed: "失败",
};
const terminal = (docs: DocOut[]) => docs.every((d) => d.status === "ready" || d.status === "failed");

export default function KbAdmin({ api }: { api: Client }) {
  const [kbId, setKbId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const [chunks, setChunks] = useState<{ doc: DocOut; rows: ChunkOut[] } | null>(null);
  const kbs = useAsync(() => call(api.GET(P.kb)) as Promise<KbOut[]>);
  const docs = usePolling(
    () => (kbId === null ? Promise.resolve([] as DocOut[])
      : call(api.GET(P.kbDocs, { params: { path: { kb_id: kbId } } })) as Promise<DocOut[]>),
    { intervalMs: 3000, stopWhen: terminal, enabled: kbId !== null });

  async function createKb() {
    if (!newName.trim()) return;
    try {
      await call(api.POST(P.kb, { body: { name: newName } }));
      setNewName("");
      kbs.reload();
    } catch (e) {
      setActionErr(e);
    }
  }

  async function onFile(f: File | undefined) {
    if (!f || kbId === null) return;
    setBusy(true);
    setActionErr(null);
    try {
      await uploadDocument(api, kbId, f);
      docs.reload();
    } catch (e) {
      setActionErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function reprocess(d: DocOut) {
    try {
      await call(api.POST(P.docReprocess, { params: { path: { doc_id: d.id } } }));
      docs.reload();
    } catch (e) {
      setActionErr(e);
    }
  }

  async function viewChunks(d: DocOut) {
    try {
      setChunks({ doc: d, rows: (await call(api.GET(P.docChunks,
        { params: { path: { doc_id: d.id } } }))) as unknown as ChunkOut[] });
    } catch (e) {
      setActionErr(e);
    }
  }

  return (
    <div className="flex gap-6 p-6">
      <aside className="w-64 shrink-0 space-y-2">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); createKb(); }}>
          <Input aria-label="新知识库名" placeholder="新知识库名" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Button type="submit" disabled={!newName.trim()}>建库</Button>
        </form>
        <ErrorBanner error={kbs.error} />
        <ul className="space-y-1">
          {(kbs.data ?? []).map((k) => (
            <li key={k.id}>
              <button className={`w-full rounded px-2 py-1 text-left text-sm hover:bg-accent ${k.id === kbId ? "bg-accent" : ""}`}
                      onClick={() => { setKbId(k.id); setChunks(null); docs.reload(); /* usePolling deps=[tick,enabled] 不感知 fn——切库必须 reload 换轮询目标（任务3评审裁决） */ }}>
                {k.name}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="min-w-0 flex-1">
        {!kbId && <p className="text-sm text-muted-foreground">选择或新建一个知识库</p>}
        {kbId !== null && (
          <>
            <div className="mb-3 flex items-center gap-3">
              <label className="text-sm">
                <input type="file" accept=".txt,.md,.pdf,.docx,.xlsx,.pptx" disabled={busy}
                       onChange={(e) => onFile(e.target.files?.[0])} />
              </label>
              {busy && <Badge variant="secondary">上传中…</Badge>}
            </div>
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left"><th className="py-1">文档</th><th>状态</th><th>大小</th><th /></tr></thead>
              <tbody>
                {(docs.data ?? []).map((d) => (
                  <tr key={d.id} className="border-b">
                    <td className="py-1">{d.name}</td>
                    <td><Badge variant={d.status === "failed" ? "destructive" : "secondary"}>{STATUS_LABEL[d.status] ?? d.status}</Badge>
                      {d.error && <p className="text-xs text-red-600">{d.error}</p>}</td>
                    <td>{d.size_bytes} B</td>
                    <td className="space-x-2 text-right">
                      {d.status === "failed" && <Button size="sm" variant="outline" onClick={() => reprocess(d)}>重试入库</Button>}
                      <Button size="sm" variant="ghost" onClick={() => viewChunks(d)}>看切块</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {docs.loading && !docs.data && <p className="text-sm text-muted-foreground">载入…</p>}
            <ErrorBanner error={docs.error ?? actionErr} />
          </>
        )}
      </section>
      {chunks && (
        <aside className="w-96 shrink-0 space-y-2 overflow-y-auto border-l bg-muted/30 p-4">
          <div className="flex justify-between">
            <h3 className="font-medium">{chunks.doc.name}：{chunks.rows.length} 块</h3>
            <Button size="sm" variant="ghost" onClick={() => setChunks(null)}>关闭</Button>
          </div>
          {chunks.rows.map((c) => (
            <pre key={c.id} className="whitespace-pre-wrap rounded bg-background p-2 text-xs">
              #{c.chunk_index}{c.has_embedding ? "" : "（无向量）"} {"\n"}{c.content.slice(0, 200)}
            </pre>
          ))}
        </aside>
      )}
    </div>
  );
}
