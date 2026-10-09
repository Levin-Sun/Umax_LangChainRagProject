import type { Metadata } from "next";
import { cookies } from "next/headers";
import localFont from "next/font/local";
import { Nav } from "@/components/Nav";
import { Providers } from "@/components/Providers";
import { THEME_COOKIE } from "@/lib/theme";
import "./globals.css";

// 自托管 Inter：`next/font/google` 会在**构建期**去 fonts.googleapis.com 取字体，
// 而交付现场常常取不到——真机 2026-10-09：docker 构建沙箱里 SSL unexpected eof / Connection reset，
// `docker compose up -d --build` 直接失败（同一台机器上普通容器却能通，只有构建期这条路走不通）。
// 自托管后构建完全离线，渲染结果不变：同一款 Inter variable、latin 子集。
// 字体与授权文本见 src/app/fonts/（SIL OFL 1.1）。
const inter = localFont({
  src: "./fonts/Inter-latin-var.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

export const metadata: Metadata = { title: "Umax 知识库问答", description: "私有化企业 RAG" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // 主题 class 由 SSR 直出（cookie 随请求到达）：首帧即正确皮肤，
  // 也避免客户端脚本改 <html> 造成 React hydration className 不匹配
  const t = (await cookies()).get(THEME_COOKIE)?.value;
  const themeClass = t === "dark" || t === "sepia" ? t : undefined;
  return (
    <html lang="zh" className={`${inter.variable}${themeClass ? ` ${themeClass}` : ""}`}>
      <body className="bg-background text-foreground antialiased">
        {/* 登录基座：AuthProvider + 401 跳登录钩子（Nav 也用 useAuth，必须在壳内） */}
        <Providers>
          <Nav />
          {children}
        </Providers>
      </body>
    </html>
  );
}
