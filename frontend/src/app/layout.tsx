import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body className={`${geistSans.variable} ${geistMono.variable} bg-background text-foreground antialiased`}>
        <nav className="flex h-12 items-center gap-4 border-b px-4 text-sm">
          <span className="font-semibold">Umax RAG</span>
          <Link href="/">聊天</Link>
          <Link href="/admin/kb">知识库</Link>
          <Link href="/admin/models">模型</Link>
          <Link href="/admin/usage">用量</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
