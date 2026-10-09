"use client";
// 聊天页：左栏会话、主区消息流、引用 [n] → chips → 右侧原文抽屉（零额外请求）
// 取数模型：只有"点击会话"才拉历史；send() 的结果存本地 turns 追加渲染——
// 避免"新会话 send 后 refetch → 本地+远端同一答案渲染两遍"。
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorBanner } from "@/components/ErrorBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { ChatOut, Citation, ConversationOut, MessageOut, QuotaOut } from "@/lib/types";

function answerParts(answer: string, citations: Citation[], onCite: (c: Citation) => void) {
  return answer.split(/(\[\d+\])/g).map((seg, i) => {
    const m = seg.match(/^\[(\d+)\]$/);
    const c = m ? citations.find((x) => x.n === Number(m[1])) : undefined;
    if (!c) return <span key={i}>{seg}</span>;
    // 引用只留序号的小角标（DeepSeek 那种）：来源名与摘录放进悬停提示，点开右侧抽屉看原文。
    // 之前每个角标都印一遍「[1] 文档名」——三个同源引用就把整段正文淹掉一半（真机反馈"太繁重"）。
    return (
      <button key={i} onClick={() => onCite(c)}
              aria-label={`引用 ${c.n}：${c.doc_name}`}
              title={`[${c.n}] ${c.doc_name}\n${c.excerpt}`}
              className="mx-0.5 inline-flex h-4.5 min-w-4.5 items-center justify-center rounded bg-muted align-middle font-mono text-caption text-ink-3 transition-colors hover:bg-accent hover:text-ink-1">
        {c.n}
      </button>
    );
  });
}

// 乐观上屏：send() 先以 out=null 的 pending turn 立即渲染提问气泡，
// POST 成功后按 key 就地填答案，失败按 key 摘除——提问不再等后端回包。
interface LocalTurn { key: number; convId: number | null; question: string; images: string[]; out: ChatOut | null }

// 兜底话术的三种成因必须说清：一律显示「资料里没有相关内容」会把"模型挂了"误导成"检索没命中"，
// 于是人去重建索引、翻文档，白忙一场（真机 2026-10-09 就这么被坑了半天）。
const DEGRADE_HINT: Record<string, string> = {
  no_hit: "检索没有命中任何内容，以上是兜底话术——确认相关文档已入库、且问法与文档用词接近。",
  no_model: "还没有可用的问答模型，以上是兜底话术——到「模型」页登记 chat 模型。",
  model_error: "模型调用失败，以上是兜底话术（不是「资料里没有」）——到「模型」页核对地址与 key，"
    + "后端日志有具体原因：docker compose logs backend",
};

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
  // 传图提问（阶段 2 放开）：随问图片以 data URL 进消息 content part
  const [pending, setPending] = useState<string[]>([]);
  const [imgErr, setImgErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // activeConv 仅由点击设置——useAsync deps 变化 = 用户点了某个会话 = 拉历史
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  const quota = useAsync(
    () => (ready ? call(api.GET(P.usageMe)) as Promise<QuotaOut>
                 : Promise.resolve(null as unknown as QuotaOut)),
    [ready]);
  const [q, setQ] = useState("");
  const convs = useAsync(
    () => (ready ? call(api.GET(P.conversations,
      { params: { query: q.trim() ? { q: q.trim() } : undefined } })) as Promise<ConversationOut[]>
                 : Promise.resolve([] as ConversationOut[])),
    [ready, q]);
  const msgs = useAsync(
    () => (!ready || activeConv === null ? Promise.resolve([] as MessageOut[])
      : call(api.GET(P.convMessages, { params: { path: { conv_id: activeConv } } })) as Promise<MessageOut[]>),
    [activeConv, ready]);

  // 新消息（本地追加的提问/答案、拉回的历史）与"检索并生成中"都要把视口带到最下面。
  // 声明在所有提前 return 之前：hook 不能有条件地调用。
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs.data, turns, sending, convId]);

  function pickImages(files: FileList | null) {
    setImgErr(null);
    const imgs: File[] = [];
    for (const f of Array.from(files ?? [])) {
      if (!/^image\/(png|jpeg|jpg|webp|gif|svg\+xml)$/.test(f.type)) {
        setImgErr(`不支持的图片格式：${f.name}（仅 PNG/JPEG/WebP/GIF/SVG）`); continue;
      }
      if (f.size > 1_500_000) { setImgErr(`图片过大（≤1.5MB）：${f.name}`); continue; }
      imgs.push(f);
    }
    const rest = pending.length + imgs.length;
    if (rest > 3) { setImgErr("最多附带 3 张图片"); return; }
    Promise.all(imgs.map((f) => new Promise<string>((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result as string);
      r.onerror = () => rej(new Error("读取文件失败"));
      r.readAsDataURL(f);
    }))).then((urls) => setPending((prev) => [...prev, ...urls])).catch(() => setImgErr("读取文件失败"));
  }

  async function delConversation(id: number) {
    try {
      await callVoid(api.DELETE(P.conversation, { params: { path: { conv_id: id } } }));
      if (id === convId) openConversation(null);   // 删的是当前打开的会话 → 回到"新建会话"态
      convs.reload();
    } catch (e) {
      setSendErr(e);
    }
  }

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
    const turnImages = pending;
    setTurns((t) => [...t, { key, convId, question: q, images: turnImages, out: null }]);
    setQuestion("");
    setPending([]);
    setSending(true);
    setSendErr(null);
    try {
      const out = (await call(api.POST(P.chat, {
        body: { question: q, conversation_id: convId, images: turnImages },
      }))) as unknown as ChatOut;
      setConvId(out.conversation_id);
      setTurns((t) => t.map((x) => (x.key === key ? { ...x, convId: out.conversation_id, out } : x)));
      setCite(null);
      convs.reload();
      quota.reload();   // 用量变了，预警条跟着更新
    } catch (e) {
      setTurns((t) => t.filter((x) => x.key !== key));
      setPending((prev) => [...prev, ...turnImages]);
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
      const q: MessageOut = { id: -1000 - i, role: "user",
        content: [{ type: "text", text: t.question },
                  ...t.images.map((u) => ({ type: "image_url" as const, image_url: { url: u } }))],
        citations: null };
      if (!t.out) return [q];
      return [q, { id: -1001 - i, role: "assistant" as const, content: [{ type: "text" as const, text: t.out.answer }], citations: t.out.citations }];
    }),
  ];
  // 只取"最后一轮"：新一轮还在等回包（out=null）时提示自然消失，不会残留上一轮的原因
  const lastTurn = [...turns].reverse().find((t) => t.convId === convId);
  const degraded = lastTurn?.out?.degraded_reason ?? null;

  return (
    <div className="flex h-[calc(100vh-3rem)]">
      <aside className="w-60 shrink-0 border-r border-border/80 bg-muted/40 px-3 py-4">
        <Button className="w-full rounded-xl border-border bg-card shadow-sm hover:bg-card/80"
                variant="outline" size="sm" onClick={() => openConversation(null)}>
          新建会话
        </Button>
        <Input aria-label="搜索会话" placeholder="搜索会话…" value={q}
               className="mt-2 h-8 rounded-lg border-border bg-card text-body"
               onChange={(e) => setQ(e.target.value)} />
        <ul className="mt-3 space-y-0.5">
          {(convs.data ?? []).map((c) => (
            <li key={c.id} className="group relative">
              <button className={`w-full truncate rounded-lg px-2.5 py-2 pr-7 text-left text-body transition-colors hover:bg-accent/60 ${c.id === convId ? "bg-card font-medium text-ink-1 shadow-sm" : "text-ink-3"}`}
                      onClick={() => openConversation(c.id)}>
                {c.title}
              </button>
              <button aria-label={`删除会话 ${c.title}`}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 hidden rounded px-1 text-caption text-ink-3 hover:text-destructive group-hover:block"
                      onClick={(e) => { e.stopPropagation(); void delConversation(c.id); }}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <div role="log" ref={logRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[760px] space-y-6 px-4 py-6">
            {shown.map((m, i) => m.role === "user" ? (
              <div key={`${m.id}:${i}`} className="flex justify-end">
                <div className="max-w-[85%] space-y-1.5 rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-body text-ink-2">
                  {m.content.map((p, j) => p.type === "image_url"
                    ? <img key={j} src={p.image_url?.url} alt="随问图片"
                           className="max-h-48 rounded-lg border border-border" />
                    : <p key={j}>{p.text}</p>)}
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
            {!sending && degraded && (
              <p role="status" className="rounded-lg bg-muted px-3 py-2 text-caption text-ink-3">
                {DEGRADE_HINT[degraded] ?? `本次回答未经过模型（${degraded}）`}
              </p>
            )}
            <ErrorBanner error={msgs.error ?? convs.error ?? sendErr} />
          </div>
        </div>
        {quota.data && (quota.data.near_limit || quota.data.exceeded) && (
          <p role="status"
             className={`mx-4 mb-2 rounded-lg px-3 py-2 text-caption ${quota.data.exceeded ? "text-destructive" : "text-ink-3"}`}>
            {quota.data.exceeded
              ? `token 配额已用尽（今日 ${quota.data.daily_used}/${quota.data.daily_limit ?? "不限"}，本月 ${quota.data.monthly_used}/${quota.data.monthly_limit ?? "不限"}），请联系管理员调整额度。`
              : `token 用量已接近上限（今日 ${quota.data.daily_used}/${quota.data.daily_limit ?? "不限"}，本月 ${quota.data.monthly_used}/${quota.data.monthly_limit ?? "不限"}）。`}
          </p>
        )}
        <form className="px-4 pb-5" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className="mx-auto w-full max-w-[760px] rounded-2xl border border-border bg-card shadow-sm transition-colors focus-within:border-ring">
            {pending.length > 0 && (
              <div className="flex flex-wrap gap-2 px-4 pt-3">
                {pending.map((u, i) => (
                  <span key={i} className="relative">
                    <img src={u} alt={`待发送图片 ${i + 1}`} className="h-14 rounded-lg border border-border" />
                    <button aria-label={`移除图片 ${i + 1}`}
                            className="absolute -right-1.5 -top-1.5 h-4.5 w-4.5 rounded-full bg-ink-3 px-1 text-caption text-card"
                            onClick={() => setPending((prev) => prev.filter((_, j) => j !== i))}>✕</button>
                  </span>
                ))}
              </div>
            )}
            {imgErr && <p className="px-4 pt-2 text-caption text-destructive">{imgErr}</p>}
            <textarea ref={taRef} aria-label="提问" rows={1}
                      placeholder="就知识库内容提问…" value={question}
                      // 生成期间不锁输入框：模型慢或卡住时，锁住的是"人"，而不是那次请求
                      // （发新消息由 send() 内的 sending 守卫拦住，不需要靠禁用输入框实现）
                      onChange={(e) => { setQuestion(e.target.value); autoGrow(); }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                      }}
                      className="block max-h-40 w-full resize-none bg-transparent px-4 pt-3.5 text-body text-ink-1 outline-none placeholder:text-ink-3" />
            <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
              <span className="flex items-center gap-1.5">
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                       multiple className="hidden"
                       onChange={(e) => { pickImages(e.target.files); e.target.value = ""; }} />
                <Button type="button" variant="ghost" size="sm" className="h-7 rounded-lg px-2 text-caption text-ink-3"
                        disabled={sending} onClick={() => fileRef.current?.click()}
                        title="随问附图（≤3 张，单张 ≤1.5MB）">
                  📎 图片
                </Button>
                <span className="select-none text-caption text-ink-3">Enter 发送 · Shift+Enter 换行</span>
              </span>
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
