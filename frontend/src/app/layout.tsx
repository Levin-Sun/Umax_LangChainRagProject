import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import { Nav } from "@/components/Nav";
import { THEME_COOKIE } from "@/lib/theme";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = { title: "Umax 知识库问答", description: "私有化企业 RAG" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // 主题 class 由 SSR 直出（cookie 随请求到达）：首帧即正确皮肤，
  // 也避免客户端脚本改 <html> 造成 React hydration className 不匹配
  const t = (await cookies()).get(THEME_COOKIE)?.value;
  const themeClass = t === "dark" || t === "sepia" ? t : undefined;
  return (
    <html lang="zh" className={themeClass}>
      <body className={`${geistSans.variable} ${geistMono.variable} bg-background text-foreground antialiased`}>
        <Nav />
        {children}
      </body>
    </html>
  );
}
