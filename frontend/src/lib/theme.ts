// 主题单一事实源 = cookie `umax_theme`（path=/）：SSR 端 layout 读它直出 <html class>，
// 客户端 applyTheme 即时改 class + 写 cookie，首帧即正确、无 hydration 错位，
// 也不需要防闪脚本。light = 无 class（与 globals.css 的 .dark/.sepia 变量块对应）。
// 隐私模式等场景 cookie 读写可能失效——吞掉，降级为仅本次会话内可切换。
export type Theme = "light" | "dark" | "sepia";

export const THEME_COOKIE = "umax_theme";

export function getStoredTheme(): Theme | null {
  try {
    const m = new RegExp(`(?:^|; )${THEME_COOKIE}=(dark|sepia|light)`).exec(document.cookie);
    return m ? (m[1] as Theme) : null;
  } catch {
    return null;
  }
}

export function storeTheme(theme: Theme): void {
  try {
    document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=${400 * 86400}; samesite=lax`;
  } catch {
    /* 存不下也要能切 */
  }
}

export function applyTheme(theme: Theme): void {
  const cl = document.documentElement.classList;
  cl.toggle("dark", theme === "dark");
  cl.toggle("sepia", theme === "sepia");
}
