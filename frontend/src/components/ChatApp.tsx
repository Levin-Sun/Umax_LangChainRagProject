"use client";
// 聊天页：左栏会话、主区消息流、引用 [n] → chips → 右侧原文抽屉（零额外请求）
// 取数模型：只有"点击会话"才拉历史；send() 的结果存本地 turns 追加渲染——
// 避免"新会话 send 后 refetch → 本地+远端同一答案渲染两遍"。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { ChatOut, Citation, ConversationOut, MessageOut } from "@/lib/types";

function answerParts(answer: string, citations: Citation[], onCite: (c: Citation) => void) {
  return answer.split(/(\[\d+\])/g).map((seg, i) => {
    const m = seg.match(/^\[(\d+)\]$/);
    const c = m ? citations.find((x) => x.n === Number(m[1])) : undefined;
    if (!c) return <span key={i}>{seg}</span>;
    return (
      <button key={i} className="mx-0.5 rounded bg-blue-50 px-1 text-xs text-blue-700 hover:bg-blue-100"
              onClick={() => onCite(c)} title={c.excerpt}>
        {`[${c.n}] ${c.doc_name}`}
      </button>
    );
  });
}

interface LocalTurn { convId: number; question: string; out: ChatOut }

export default function ChatApp({ api }: { api: Client }) {
  const [convId, setConvId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [sendErr, setSendErr] = useState<unknown>(null);
  const [turns, setTurns] = useState<LocalTurn[]>([]);
  const [cite, setCite] = useState<Citation | null>(null);
  // activeConv 仅由点击设置——useAsync deps 变化 = 用户点了某个会话 = 拉历史
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const convs = useAsync(() => call(api.GET(P.conversations)) as Promise<ConversationOut[]>);
  const msgs = useAsync(
    () => (activeConv === null ? Promise.resolve([] as MessageOut[])
      : call(api.GET(P.convMessages, { params: { path: { conv_id: activeConv } } })) as Promise<MessageOut[]>),
    [activeConv]);

  function openConversation(id: number | null) {
    setConvId(id);
    setActiveConv(id);
    setTurns([]);
    setCite(null);
    setSendErr(null);
  }

  async function send() {
    const q = question.trim();
    if (!q || sending) return;
    setSending(true);
    setSendErr(null);
    try {
      const out = (await call(api.POST(P.chat, {
        body: { question: q, conversation_id: convId },
      }))) as unknown as ChatOut;
      setConvId(out.conversation_id);
      setTurns((t) => [...t, { convId: out.conversation_id, question: q, out }]);
      setQuestion("");
      setCite(null);
      convs.reload();
    } catch (e) {
      setSendErr(e);
    } finally {
      setSending(false);
    }
  }

  const shown: MessageOut[] = [
    ...(msgs.data ?? []),
    ...turns.filter((t) => t.convId === convId).flatMap((t, i) => [
      { id: -1000 - i, role: "user" as const, content: [{ type: "text", text: t.question }], citations: null },
      { id: -1001 - i, role: "assistant" as const, content: [{ type: "text", text: t.out.answer }], citations: t.out.citations },
    ]),
  ];

  return (
    <div className="flex h-[calc(100vh-3rem)]">
      <aside className="w-56 shrink-0 border-r p-2">
        <Button className="w-full" variant="outline" onClick={() => openConversation(null)}>
          新建会话
        </Button>
        <ul className="mt-2 space-y-1">
          {(convs.data ?? []).map((c) => (
            <li key={c.id}>
              <button className={`w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-accent ${c.id === convId ? "bg-accent" : ""}`}
                      onClick={() => openConversation(c.id)}>
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {shown.map((m, i) => (
            <div key={`${m.id}:${i}`} className={m.role === "user" ? "text-right" : ""}>
              <div className={`inline-block max-w-[80%] rounded-lg border p-3 text-left text-sm ${m.role === "user" ? "bg-muted" : ""}`}>
                {m.content.map((p, j) => p.type === "text" && (
                  <p key={j}>{m.role === "assistant" && m.citations
                    ? answerParts(p.text ?? "", m.citations, setCite)
                    : p.text}</p>
                ))}
              </div>
            </div>
          ))}
          {sending && <p className="text-sm text-muted-foreground">检索并生成中…</p>}
          <ErrorBanner error={msgs.error ?? convs.error ?? sendErr} />
        </div>
        <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <Input aria-label="提问" placeholder="就知识库内容提问…" value={question}
                 onChange={(e) => setQuestion(e.target.value)} disabled={sending} />
          <Button type="submit" disabled={!question.trim() || sending}>发送</Button>
        </form>
      </main>
      {cite && (
        <aside className="w-80 shrink-0 space-y-2 border-l bg-muted/30 p-4">
          <div className="flex items-center justify-between">
            <Badge>{`[${cite.n}] ${cite.doc_name}`}</Badge>
            <Button size="sm" variant="ghost" onClick={() => setCite(null)}>关闭</Button>
          </div>
          <p className="text-sm">chunk #{cite.chunk_id}</p>
          <blockquote className="border-l-2 pl-2 text-sm text-muted-foreground">{cite.excerpt}</blockquote>
        </aside>
      )}
    </div>
  );
}
