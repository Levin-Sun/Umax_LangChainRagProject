"use client";
// 知识库后台：kb 列表/新建（无删除端点，不做）→ 选中后文档表轮询 + 上传 + reprocess + chunks 抽屉
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, uploadDocument, uploadDocuments, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync, usePolling } from "@/lib/hooks";
import type { BatchUploadItem, ChunkOut, DocOut, KbOut } from "@/lib/types";

const fmtBytes = (n: number | null) => {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

// 上传时间按本地时区展示到分钟：客户看的是"什么时候传的"，秒级精度没有意义。
// 用紧凑固定格式而非 locale 默认（后者会给 "10/08/2026, 01:55 AM"，更长且在窄屏把表格撑出横向滚动）
const fmtTime = (iso: string) => {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const STATUS_LABEL: Record<string, string> = {
  pending: "排队中", parsing: "解析中", ready: "就绪", failed: "失败",
};
const terminal = (docs: DocOut[]) => docs.every((d) => d.status === "ready" || d.status === "failed");

export default function KbAdmin({ api }: { api: Client }) {
  const [kbId, setKbId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [batchErrors, setBatchErrors] = useState<string[]>([]);
  const [deleteFor, setDeleteFor] = useState<DocOut | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
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

  async function onFiles(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (!files.length || kbId === null) return;
    setBusy(true);
    setActionErr(null);
    setBatchErrors([]);
    try {
      if (files.length === 1) {
        await uploadDocument(api, kbId, files[0]);
      } else {
        // 批量：部分成功语义——坏文件（不支持的类型等）只在这里逐行提示，其余照常入库
        const items: BatchUploadItem[] = await uploadDocuments(api, kbId, files);
        const bad = items.filter((i) => i.error).map((i) => `${i.name}：${i.error}`);
        if (bad.length) setBatchErrors(bad);
      }
      docs.reload();
    } catch (e) {
      setActionErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function del(d: DocOut) {
    setDeleteFor(null);
    try {
      await callVoid(api.DELETE(P.doc, { params: { path: { doc_id: d.id } } }));
      docs.reload();
    } catch (e) {
      setActionErr(e);
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
    // 抽屉占固定宽（360）+ 库列表（224）：容器仍卡 max-w-5xl 时中间列只剩 ~370px，
    // 表格最小内容宽度撑破所在列、直接画到抽屉上（真机踩过）。开抽屉时放宽上限。
    <div className={`mx-auto flex w-full gap-6 px-6 py-6 ${chunks ? "max-w-[1700px]" : "max-w-5xl"}`}>
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
            <div role="group" aria-label="文档上传"
                 onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
                 onDragLeave={() => setDragging(false)}
                 onDrop={(e) => {
                   e.preventDefault();
                   setDragging(false);
                   if (!busy) void onFiles(e.dataTransfer?.files ?? null);
                 }}
                 className={`flex flex-wrap items-center gap-3 rounded-lg border border-dashed px-4 py-3 transition-colors ${
                   dragging ? "border-ring bg-accent/40" : "border-border"}`}>
              {/* 隐藏的原生控件仍是无障碍与测试的真实入口；可见入口只有一个按钮，避免两处入口的困惑 */}
              <input ref={fileRef} type="file" accept=".txt,.md,.pdf,.docx,.xlsx,.pptx" multiple
                     aria-label="上传文档" disabled={busy} className="sr-only"
                     onChange={(e) => { void onFiles(e.target.files); e.target.value = ""; }} />
              <Button type="button" variant="outline" className="h-9 rounded-lg" disabled={busy}
                      onClick={() => fileRef.current?.click()}>选择文件</Button>
              <span className="text-caption text-ink-3">
                或把文件拖到这里 · 可多选，一次最多 20 个文件
              </span>
              {busy && <Badge variant="secondary">上传中…</Badge>}
            </div>
            {batchErrors.length > 0 && (
              <ul role="alert" className="space-y-0.5 rounded-lg border border-border bg-muted/60 px-3 py-2">
                {batchErrors.map((m) => (
                  <li key={m} className="text-caption text-destructive">{m}</li>
                ))}
              </ul>
            )}
            {/* overflow-x-auto：空间被压到极限时表格在自己盒子内滚动，绝不溢出压到抽屉上 */}
            <div className="overflow-x-auto">
              <table className="w-full text-body text-ink-2">
                <thead><tr className="border-b border-border text-left text-h3 font-medium text-ink-2">
                  <th className="whitespace-nowrap py-2 pr-3 font-medium xl:pr-4">文档</th>
                  <th className="whitespace-nowrap pr-3 font-medium xl:pr-4">状态</th>
                  <th className="whitespace-nowrap pr-3 text-right font-medium xl:pr-4">大小</th>
                  <th className="whitespace-nowrap pr-3 font-medium xl:pr-4">上传时间</th><th /></tr></thead>
              <tbody>
                {(docs.data ?? []).map((d) => (
                  <tr key={d.id} className="border-b border-border last:border-0">
                    <td className="py-2.5 pr-3 xl:pr-4">
                      {/* max-w 必须落在单元格内层 block 上：auto 表格布局会忽略 td 自身的 max-width */}
                      <span className="block max-w-40 truncate xl:max-w-72" title={d.name}>{d.name}</span>
                    </td>
                    <td className="pr-3 xl:pr-4"><Badge variant={d.status === "failed" ? "destructive" : "secondary"} className="whitespace-nowrap rounded-full font-normal">
                      {STATUS_LABEL[d.status] ?? d.status}</Badge>
                      {d.error && <p className="mt-0.5 max-w-60 truncate text-caption text-destructive" title={d.error}>{d.error}</p>}</td>
                    <td className="whitespace-nowrap pr-3 text-right font-medium xl:pr-4">{fmtBytes(d.size_bytes)}</td>
                    <td className="whitespace-nowrap pr-3 text-ink-3 xl:pr-4">{fmtTime(d.created_at)}</td>
                    <td className="space-x-1 text-right">
                      {d.status === "failed" && <Button size="sm" variant="outline" className="h-7 rounded-lg" onClick={() => reprocess(d)}>重试入库</Button>}
                      <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-1" onClick={() => viewChunks(d)}>看切块</Button>
                      <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive hover:text-destructive"
                              onClick={() => setDeleteFor(d)}>删除</Button>
                    </td>
                  </tr>
                ))}
                </tbody>
              </table>
            </div>
            {docs.loading && !docs.data && <p className="text-caption text-ink-3">载入…</p>}
            <AdminBanner error={docs.error} />
          </div>
        )}
      </section>
      {deleteFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
             onClick={() => setDeleteFor(null)}>
          <div role="dialog" aria-label={`删除文档 ${deleteFor.name}`}
               onClick={(e) => e.stopPropagation()}
               className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">删除文档</h3>
            <p className="text-body text-ink-2">
              确定删除「{deleteFor.name}」？该文档的切块与索引会一并清除，重新使用需再次上传。
            </p>
            <div className="flex gap-2">
              <Button size="sm" className="h-8 rounded-lg bg-destructive text-card hover:bg-destructive/90"
                      onClick={() => void del(deleteFor)}>确认删除</Button>
              <Button size="sm" variant="ghost" className="h-8 rounded-lg text-ink-3"
                      onClick={() => setDeleteFor(null)}>取消</Button>
            </div>
          </div>
        </div>
      )}
      {chunks && (
        <aside className="w-[340px] shrink-0 space-y-3 overflow-y-auto border-l border-border/80 p-4 xl:w-[360px]">
          <div className="flex items-center justify-between">
            <h3 className="text-h3 font-medium text-ink-2">{chunks.doc.name}：{chunks.rows.length} 块</h3>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-ink-3" onClick={() => setChunks(null)}>关闭</Button>
          </div>
          <p className="text-caption text-ink-3">
            带「图片描述」标记的块来自文档内嵌图表/照片（视觉模型转文字后入库）。
          </p>
          {chunks.rows.map((c) => (
            <pre key={c.id} className="whitespace-pre-wrap rounded-lg border border-border bg-card p-3 font-mono text-caption text-ink-2">
              #{c.chunk_index}{c.has_embedding ? "" : "（无向量）"}
              {c.meta?.source === "image" ? `（图片描述 · ${String(c.meta.image_origin ?? "")}）` : ""}
              {"\n"}{c.content.slice(0, 200)}
            </pre>
          ))}
        </aside>
      )}
    </div>
  );
}
