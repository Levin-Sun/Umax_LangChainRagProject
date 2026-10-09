"use client";
// 可搜索下拉（厂商 / 模型选择用）。
//
// 为什么不用原生 <select>：展开列表由系统画（方角、系统蓝条，样式管不到），而且做不到
// "聚焦即展示全部、输入即过滤"。与 components/ui/select.tsx 的分工：
//   Select   = 选项少且固定的枚举（场景、接入方式）
//   Combobox = 选项多、需要搜索（厂商标本、模型清单），并匹配别名与拼音（客户会打 de / 百炼 / abl）
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface ComboOption {
  value: string;
  label: string;
  keywords?: string[];     // 别名 + 拼音（客户实际会怎么打字）
  hint?: string;           // 右侧补充（如"已实测"/"1024 维"）
}

export function Combobox({ value, options, onChange, label, placeholder, disabled, className }: {
  value: string;
  options: readonly ComboOption[];
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const listId = useId();

  const current = options.find((o) => o.value === value);
  const hits = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [...options].sort((a, b) => a.label.localeCompare(b.label, "zh"));
    const rank = (o: ComboOption) => {
      const text = o.label.toLowerCase();
      if (text.startsWith(needle)) return 3;
      if (text.includes(needle)) return 2;
      return (o.keywords ?? []).some((k) => k.toLowerCase().includes(needle)) ? 1 : 0;
    };
    return options.map((o) => ({ o, s: rank(o) })).filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s || a.o.label.localeCompare(b.o.label, "zh")).map((x) => x.o);
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);
  useEffect(() => setActive(0), [query, open]);

  function pick(o: ComboOption) {
    onChange(o.value);
    setOpen(false);
    setQuery("");
  }

  return (
    <div ref={box} className={cn("relative", className)}>
      <div className="flex h-9 items-center gap-2 rounded-lg border border-border bg-card px-3 focus-within:border-ring">
        <input aria-label={label} role="combobox" aria-expanded={open} aria-controls={listId}
               disabled={disabled} placeholder={placeholder}
               className="w-full bg-transparent text-body text-ink-1 outline-none placeholder:text-ink-3"
               value={open ? query : current?.label ?? ""}
               onFocus={() => { setQuery(""); setOpen(true); }}
               onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
               onKeyDown={(e) => {
                 if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                   e.preventDefault();
                   setOpen(true);
                   setActive((i) => (e.key === "ArrowDown"
                     ? Math.min(i + 1, hits.length - 1) : Math.max(i - 1, 0)));
                 } else if (e.key === "Enter") {
                   if (open && hits[active]) { e.preventDefault(); pick(hits[active]); }
                 } else if (e.key === "Escape") {
                   setOpen(false);
                   setQuery("");
                 }
               }} />
        <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 shrink-0 text-ink-3"
             fill="none" stroke="currentColor" strokeWidth="1.6"
             strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 6.5 8 10.5 12 6.5" />
        </svg>
      </div>
      {open && (
        <ul id={listId} role="listbox" aria-label={label}
            className="absolute left-0 right-0 z-50 mt-1 max-h-64 overflow-y-auto rounded-xl border border-border bg-popover p-1 shadow-lg">
          {hits.map((o, i) => (
            <li key={o.value} role="option" aria-selected={o.value === value}
                onPointerDown={(e) => { e.preventDefault(); pick(o); }}
                className={cn("flex cursor-pointer items-baseline gap-2 rounded-lg px-2.5 py-1.5 text-body",
                              i === active ? "bg-accent" : "hover:bg-accent/70",
                              o.value === value ? "font-medium text-ink-1" : "text-ink-2")}>
              <span className="truncate">{o.label}</span>
              {o.hint && <span className="ml-auto shrink-0 text-caption text-ink-3">{o.hint}</span>}
            </li>
          ))}
          {!hits.length && <li className="px-2.5 py-1.5 text-caption text-ink-3">没有匹配项</li>}
        </ul>
      )}
    </div>
  );
}
