import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Inter } from "next/font/google";
import { Nav } from "@/components/Nav";
import { Providers } from "@/components/Providers";
import { THEME_COOKIE } from "@/lib/theme";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
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
