"use client";
// 自绘下拉（替代原生 <select>）。
//
// 为什么不用原生控件：闭合的框能靠 appearance:none 修圆角，但**展开列表由浏览器/系统单独画**，
// author 管不到——方角、整条系统蓝的选中条，跟其它圆润的卡片/输入框明显不是一套
// （真机反馈连续两轮，第二轮的截图里列表仍是系统样式）。所以列表也自己画：
// 与卡片同圆角（rounded-xl + 阴影）、选项 hover/选中走主题色，深色主题天然一致。
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface SelectOption { value: string; label: string }

export function Select({ value, options, onChange, label, disabled, className }: {
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  label?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // 点外部/按 Esc 关闭：列表是 absolute 浮层，不关会一直悬在表格上
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = options.find((o) => o.value === value);

  return (
    <div ref={box} className={cn("relative", className)}>
      <button type="button" aria-label={label} aria-haspopup="listbox" aria-expanded={open}
              disabled={disabled}
              onClick={() => setOpen((o) => !o)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpen(true);
                }
              }}
              className="flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 text-body text-ink-1 outline-none transition-colors focus:border-ring disabled:cursor-not-allowed disabled:opacity-50">
        <span className="truncate">{current?.label ?? value}</span>
        <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 shrink-0 text-ink-3"
             fill="none" stroke="currentColor" strokeWidth="1.6"
             strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 6.5 8 10.5 12 6.5" />
        </svg>
      </button>
      {open && (
        <ul role="listbox" aria-label={label}
            className="absolute left-0 right-0 z-50 mt-1 max-h-64 overflow-y-auto rounded-xl border border-border bg-popover p-1 shadow-lg">
          {options.map((o) => (
            <li key={o.value} role="option" aria-selected={o.value === value}
                onClick={() => { onChange(o.value); setOpen(false); }}
                className={cn(
                  "cursor-pointer truncate rounded-lg px-2.5 py-1.5 text-body transition-colors",
                  o.value === value ? "bg-accent font-medium text-ink-1" : "text-ink-2 hover:bg-accent/70",
                )}>
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
