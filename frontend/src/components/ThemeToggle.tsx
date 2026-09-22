"use client";
// Nav 右侧皮肤切换：状态以 localStorage 为准（与 layout 防闪 script 同源）。
// useState 不能直接读 storage 初始化：SSR 端无 window，hydration 会把服务端算出的
// "light" 锁进状态，按钮高亮与实际主题脱节（真机走查实测）——挂载后 effect 同步。
import { useEffect, useState } from "react";
import { applyTheme, getStoredTheme, storeTheme, type Theme } from "@/lib/theme";

const THEMES: Array<[Theme, string]> = [
  ["light", "浅色"],
  ["dark", "深色"],
  ["sepia", "护眼"],
];

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => { setTheme(getStoredTheme() ?? "light"); }, []);

  function pick(t: Theme) {
    applyTheme(t);
    storeTheme(t);
    setTheme(t);
  }

  return (
    <div className="flex items-center gap-0.5 rounded-lg border border-border p-0.5">
      {THEMES.map(([t, label]) => (
        <button key={t} aria-pressed={theme === t} onClick={() => pick(t)}
                className={`rounded-md px-2 py-1 text-xs transition-colors ${
                  theme === t
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/60"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}
