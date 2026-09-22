"use client";
// 知识库后台：kb 列表/新建（无删除端点，不做）→ 选中后文档表轮询 + 上传 + reprocess + chunks 抽屉
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import AdminBanner from "@/components/AdminBanner";
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
    setActionErr(null);  // 任务7欠账③：入口先清残留旧错，否则新一次失败前横幅一直挂着上次的错
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
    <div className="mx-auto flex w-full max-w-5xl gap-6 px-6 py-6">
      <aside className="w-56 shrink-0 space-y-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); createKb(); }}>
          <Input aria-label="新知识库名" placeholder="新知识库名" className="h-9 rounded-lg border-border"
                 value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Button type="submit" size="sm" disabled={!newName.trim()} className="h-9 shrink-0 rounded-lg px-3">建库</Button>
        </form>
        {/* 任务7欠账③：actionErr 挪到左栏常驻横幅——建库失败时往往还没选库，
            旧版藏在 kbId!==null 块里根本看不见 */}
        <AdminBanner error={kbs.error ?? actionErr} />
        <ul className="space-y-0.5">
          {(kbs.data ?? []).map((k) => (
            <li key={k.id}>
              <button className={`w-full truncate rounded-lg px-2.5 py-2 text-left text-body transition-colors hover:bg-accent/60 ${k.id === kbId ? "bg-card font-medium text-ink-1 shadow-sm" : "text-ink-3"}`}
                      onClick={() => { setKbId(k.id); setChunks(null); docs.reload(); /* usePolling deps=[tick,enabled] 不感知 fn——切库必须 reload 换轮询目标（任务3评审裁决） */ }}>
                {k.name}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="min-w-0 flex-1">
        {!kbId && (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-border text-caption text-ink-3">
            选择或新建一个知识库
          </div>
        )}
        {kbId !== null && (
          <div className="space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
            <div className="flex items-center gap-3">
              <label className="text-body text-ink-2">
                <input type="file" accept=".txt,.md,.pdf,.docx,.xlsx,.pptx" disabled={busy}
                       className="text-body file:mr-3 file:cursor-pointer file:rounded-lg file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-body hover:file:bg-accent"
                       onChange={(e) => onFile(e.target.files?.[0])} />
              </label>
              {busy && <Badge variant="secondary">上传中…</Badge>}
            </div>
            <table className="w-full text-body text-ink-2">
              <thead><tr className="border-b border-border text-left text-h3 font-medium text-ink-2">
                <th className="py-2 font-medium">文档</th><th className="font-medium">状态</th><th className="font-medium">大小</th><th /></tr></thead>
              <tbody>
                {(docs.data ?? []).map((d) => (
                  <tr key={d.id} className="border-b border-border last:border-0">
                    <td className="py-2.5">{d.name}</td>
                    <td><Badge variant={d.status === "failed" ? "destructive" : "secondary"} className="rounded-full font-normal">
                      {STATUS_LABEL[d.status] ?? d.status}</Badge>
                      {d.error && <p className="mt-0.5 max-w-60 truncate text-caption text-destructive" title={d.error}>{d.error}</p>}</td>
                    <td className="font-medium">{d.size_bytes} B</td>
                    <td className="space-x-1 text-right">
                      {d.status === "failed" && <Button size="sm" variant="outline" className="h-7 rounded-lg" onClick={() => reprocess(d)}>重试入库</Button>}
                      <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-1" onClick={() => viewChunks(d)}>看切块</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {docs.loading && !docs.data && <p className="text-caption text-ink-3">载入…</p>}
            <AdminBanner error={docs.error} />
          </div>
        )}
      </section>
      {chunks && (
        <aside className="w-[380px] shrink-0 space-y-3 overflow-y-auto border-l border-border/80 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-h3 font-medium text-ink-2">{chunks.doc.name}：{chunks.rows.length} 块</h3>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-ink-3" onClick={() => setChunks(null)}>关闭</Button>
          </div>
          {chunks.rows.map((c) => (
            <pre key={c.id} className="whitespace-pre-wrap rounded-lg border border-border bg-card p-3 font-mono text-caption text-ink-2">
              #{c.chunk_index}{c.has_embedding ? "" : "（无向量）"} {"\n"}{c.content.slice(0, 200)}
            </pre>
          ))}
        </aside>
      )}
    </div>
  );
}
