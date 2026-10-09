"use client";
// 助手回答的排版。模型（尤其 DeepSeek）默认输出 Markdown，而此前我们按纯文本渲染，
// 于是 **粗体** 显示成星号、`行内代码` 显示成反引号、短横线列表跟正文糊成一段
// （真机反馈："换成 DeepSeek 后回答快很多，但可读性太差"）。
//
// 只渲染**安全子集**，并且全部走 React 节点（不用 innerHTML）：模型输出里夹 HTML 也只会当文字显示。
// 子集：段落与换行、`- / *` 与 `1.` 列表、`#` 标题、**粗体**、`行内代码`、以及本产品的 [n] 引用角标。
// 不引第三方 markdown 库：这点子集足够覆盖模型的实际输出，零新依赖、也没有 XSS 面。
import type { ReactNode } from "react";
import type { Citation } from "@/lib/types";

type Block =
  | { kind: "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "h"; text: string };

const HEADING = /^#{1,4}\s+/;
const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.、)]\s+/;
// 一次扫描覆盖三类行内标记：引用角标 | **粗体** | `代码`。粗体内容不允许跨 * ，
// 免得模型漏写一个星号就把后面整段吞掉（宁可少排版，不要排版错）。
const INLINE = /\[(\d+)\]|\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;

function toBlocks(raw: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "p", text: para.join("\n") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };

  for (const rawLine of raw.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {          // 空行 = 段落/列表的边界
      flushPara();
      flushList();
      continue;
    }
    if (HEADING.test(line)) {
      flushPara();
      flushList();
      blocks.push({ kind: "h", text: line.replace(HEADING, "").trim() });
      continue;
    }
    if (BULLET.test(line)) {
      flushPara();
      if (!list || list.kind !== "ul") { flushList(); list = { kind: "ul", items: [] }; }
      list.items.push(line.replace(BULLET, "").trim());
      continue;
    }
    if (NUMBERED.test(line)) {
      flushPara();
      if (!list || list.kind !== "ol") { flushList(); list = { kind: "ol", items: [] }; }
      list.items.push(line.replace(NUMBERED, "").trim());
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara();
  flushList();
  return blocks;
}

/** 合并同源角标：紧邻且指向同一篇文档的 [n] 并成一个「1-3」。
 *  用户拍板的呈现方式；不同来源不合并（合成一格等于丢掉出处）。 */
function mergedChip(group: Citation[], onCite: (c: Citation) => void, key: string) {
  const head = group[0];
  const label = group.length > 1 ? `${head.n}-${group[group.length - 1].n}` : String(head.n);
  return (
    <button key={key} onClick={() => onCite(head)}
            aria-label={`引用 ${label}：${head.doc_name}`}
            title={group.length > 1
              ? `[${label}] ${head.doc_name} · 同一篇文档的 ${group.length} 段\n${head.excerpt}`
              : `[${head.n}] ${head.doc_name}\n${head.excerpt}`}
            className="mx-0.5 inline-flex h-4.5 min-w-4.5 items-center justify-center rounded bg-muted align-middle font-mono text-caption text-ink-3 transition-colors hover:bg-accent hover:text-ink-1">
      {label}
    </button>
  );
}

function inline(text: string, citations: Citation[], onCite: (c: Citation) => void,
                keyBase: string): ReactNode[] {
  // 先切词，再渲染：切词这步才看得见"两个角标是不是紧邻"，渲染这步才好合并同源。
  type Token = { kind: "text"; text: string } | { kind: "cite"; c: Citation | null; raw: string }
    | { kind: "bold"; text: string } | { kind: "code"; text: string };
  const tokens: Token[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) tokens.push({ kind: "text", text: text.slice(last, m.index) });
    if (m[1] !== undefined) {
      tokens.push({ kind: "cite", c: citations.find((x) => x.n === Number(m[1])) ?? null, raw: m[0] });
    } else if (m[2] !== undefined) {
      tokens.push({ kind: "bold", text: m[2] });
    } else {
      tokens.push({ kind: "code", text: m[3] });
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) tokens.push({ kind: "text", text: text.slice(last) });

  const nodes: ReactNode[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.kind === "text") { nodes.push(t.text); continue; }
    if (t.kind === "bold") {
      nodes.push(<strong key={`${keyBase}-b${i}`} className="font-medium text-ink-1">{t.text}</strong>);
      continue;
    }
    if (t.kind === "code") {
      nodes.push(<code key={`${keyBase}-k${i}`}
                       className="rounded bg-muted px-1 font-mono text-caption text-ink-2">{t.text}</code>);
      continue;
    }
    if (!t.c) { nodes.push(t.raw); continue; }   // 解析不到就原样显示，不吞内容
    const group = [t.c];
    while (i + 1 < tokens.length) {
      const next = tokens[i + 1];
      if (next.kind !== "cite" || !next.c || next.c.doc_name !== t.c.doc_name) break;
      group.push(next.c);
      i += 1;
    }
    nodes.push(mergedChip(group, onCite, `${keyBase}-c${i}`));
  }
  return nodes;
}

export function AnswerBody({ text, citations, onCite }: {
  text: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const blocks = toBlocks(text);
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => {
        const key = `b${i}`;
        // 段内保留换行（whitespace-pre-wrap）：模型常用单换行分行，不保留就会糊成一坨
        if (b.kind === "p") {
          return <p key={key} className="whitespace-pre-wrap">{inline(b.text, citations, onCite, key)}</p>;
        }
        if (b.kind === "h") {
          return <h3 key={key} className="text-h3 font-medium text-ink-1">{inline(b.text, citations, onCite, key)}</h3>;
        }
        const cls = b.kind === "ul" ? "list-disc" : "list-decimal";
        const items = b.items.map((it, j) =>
          <li key={`${key}-${j}`}>{inline(it, citations, onCite, `${key}-${j}`)}</li>);
        return b.kind === "ul"
          ? <ul key={key} className={`${cls} space-y-1 pl-5`}>{items}</ul>
          : <ol key={key} className={`${cls} space-y-1 pl-5`}>{items}</ol>;
      })}
    </div>
  );
}
