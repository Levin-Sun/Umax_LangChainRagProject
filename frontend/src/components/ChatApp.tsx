"use client";
// 聊天页：左栏会话、主区消息流、引用 [n] → chips → 右侧原文抽屉（零额外请求）
// 取数模型：只有"点击会话"才拉历史；send() 的结果存本地 turns 追加渲染——
// 避免"新会话 send 后 refetch → 本地+远端同一答案渲染两遍"。
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, type Client } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { ChatOut, Citation, ConversationOut, MessageOut } from "@/lib/types";

function answerParts(answer: string, citations: Citation[], onCite: (c: Citation) => void) {
  return answer.split(/(\[\d+\])/g).map((seg, i) => {
    const m = seg.match(/^\[(\d+)\]$/);
    const c = m ? citations.find((x) => x.n === Number(m[1])) : undefined;
    if (!c) return <span key={i}>{seg}</span>;
    return (
      <button key={i} className="mx-0.5 rounded px-1 font-mono text-caption text-ink-3 transition-colors hover:bg-accent hover:underline"
              onClick={() => onCite(c)} title={c.excerpt}>
        {`[${c.n}] ${c.doc_name}`}
      </button>
    );
  });
}

// 乐观上屏：send() 先以 out=null 的 pending turn 立即渲染提问气泡，
// POST 成功后按 key 就地填答案，失败按 key 摘除——提问不再等后端回包。
interface LocalTurn { key: number; convId: number | null; question: string; out: ChatOut | null }

export default function ChatApp({ api }: { api: Client }) {
  // 任务 7 换轨：/auth/me 就绪前不发业务请求（匿名只会处处 401）；loaded 无 me → 弹回登录页。
  const { me, loaded } = useAuth();
  const router = useRouter();
  const ready = loaded && me !== null;
  useEffect(() => {
    if (loaded && !me) router.replace("/admin/login");
  }, [loaded, me, router]);
  const [convId, setConvId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [sendErr, setSendErr] = useState<unknown>(null);
  const [turns, setTurns] = useState<LocalTurn[]>([]);
  const [cite, setCite] = useState<Citation | null>(null);
  // activeConv 仅由点击设置——useAsync deps 变化 = 用户点了某个会话 = 拉历史
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const seq = useRef(0);
  const convs = useAsync(
    () => (ready ? call(api.GET(P.conversations)) as Promise<ConversationOut[]>
                 : Promise.resolve([] as ConversationOut[])),
    [ready]);
  const msgs = useAsync(
    () => (!ready || activeConv === null ? Promise.resolve([] as MessageOut[])
      : call(api.GET(P.convMessages, { params: { path: { conv_id: activeConv } } })) as Promise<MessageOut[]>),
    [activeConv, ready]);

  function openConversation(id: number | null) {
    // 任务7欠账①：点击当前已打开会话 = no-op（旧实现无条件 setTurns([]) 把本地
    // 追加的答案清空，历史又要靠后端重取，出现空白窗口）。"新建会话"（null）仍重置。
    if (id !== null && id === convId) return;
    setConvId(id);
    setActiveConv(id);
    setTurns([]);
    setCite(null);
    setSendErr(null);
  }

  async function send() {
    const q = question.trim();
    if (!q || sending) return;
    const key = ++seq.current;
    setTurns((t) => [...t, { key, convId, question: q, out: null }]);
    setQuestion("");
    setSending(true);
    setSendErr(null);
    try {
      const out = (await call(api.POST(P.chat, {
        body: { question: q, conversation_id: convId },
      }))) as unknown as ChatOut;
      setConvId(out.conversation_id);
      setTurns((t) => t.map((x) => (x.key === key ? { ...x, convId: out.conversation_id, out } : x)));
      setCite(null);
      convs.reload();
    } catch (e) {
      setTurns((t) => t.filter((x) => x.key !== key));
      setSendErr(e);
    } finally {
      setSending(false);
      taRef.current?.focus();
    }
  }

  function autoGrow() {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  if (!loaded) {
    // me 未定的首帧只有骨架：未登录态不闪出空会话列表/输入框
    return (
      <div className="flex h-[calc(100vh-3rem)] items-center justify-center">
        <p className="text-caption text-ink-3">加载中…</p>
      </div>
    );
  }
  if (!me) return null;  // effect 已弹回登录页，本页不再渲染

  const shown: MessageOut[] = [
    ...(msgs.data ?? []),
    ...turns.filter((t) => t.convId === convId).flatMap((t, i) => {
      const q: MessageOut = { id: -1000 - i, role: "user", content: [{ type: "text", text: t.question }], citations: null };
      if (!t.out) return [q];
      return [q, { id: -1001 - i, role: "assistant" as const, content: [{ type: "text" as const, text: t.out.answer }], citations: t.out.citations }];
    }),
  ];

  return (
    <div className="flex h-[calc(100vh-3rem)]">
      <aside className="w-60 shrink-0 border-r border-border/80 bg-muted/40 px-3 py-4">
        <Button className="w-full rounded-xl border-border bg-card shadow-sm hover:bg-card/80"
                variant="outline" size="sm" onClick={() => openConversation(null)}>
          新建会话
        </Button>
        <ul className="mt-3 space-y-0.5">
          {(convs.data ?? []).map((c) => (
            <li key={c.id}>
              <button className={`w-full truncate rounded-lg px-2.5 py-2 text-left text-body transition-colors hover:bg-accent/60 ${c.id === convId ? "bg-card font-medium text-ink-1 shadow-sm" : "text-ink-3"}`}
                      onClick={() => openConversation(c.id)}>
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <div role="log" className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[760px] space-y-6 px-4 py-6">
            {shown.map((m, i) => m.role === "user" ? (
              <div key={`${m.id}:${i}`} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-body text-ink-2">
                  {m.content.map((p, j) => p.type === "text" && <p key={j}>{p.text}</p>)}
                </div>
              </div>
            ) : (
              <div key={`${m.id}:${i}`} className="max-w-full text-body text-ink-2">
                {m.content.map((p, j) => p.type === "text" && (
                  <p key={j} className="mb-2 last:mb-0">{m.role === "assistant" && m.citations
                    ? answerParts(p.text ?? "", m.citations, setCite)
                    : p.text}</p>
                ))}
              </div>
            ))}
            {sending && <p className="text-caption text-ink-3">检索并生成中…</p>}
            <ErrorBanner error={msgs.error ?? convs.error ?? sendErr} />
          </div>
        </div>
        <form className="px-4 pb-5" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className="mx-auto w-full max-w-[760px] rounded-2xl border border-border bg-card shadow-sm transition-colors focus-within:border-ring">
            <textarea ref={taRef} aria-label="提问" rows={1}
                      placeholder="就知识库内容提问…" value={question}
                      disabled={sending}
                      onChange={(e) => { setQuestion(e.target.value); autoGrow(); }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                      }}
                      className="block max-h-40 w-full resize-none bg-transparent px-4 pt-3.5 text-body text-ink-1 outline-none placeholder:text-ink-3" />
            <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
              <span className="select-none text-caption text-ink-3">Enter 发送 · Shift+Enter 换行</span>
              <Button type="submit" size="sm" disabled={!question.trim() || sending}
                      className="h-8 rounded-full px-4">
                发送
              </Button>
            </div>
          </div>
        </form>
      </main>
      {cite && (
        <aside className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-border/80 p-4">
          <div className="flex items-center justify-between">
            <p className="max-w-[80%] truncate font-mono text-caption text-ink-3">{`[${cite.n}] ${cite.doc_name}`}</p>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-ink-2" onClick={() => setCite(null)}>关闭</Button>
          </div>
          <p className="font-mono text-caption text-ink-3">chunk #{cite.chunk_id}</p>
          <blockquote className="whitespace-pre-wrap border-l-2 border-border pl-3 text-body text-ink-2">{cite.excerpt}</blockquote>
        </aside>
      )}
    </div>
  );
}
