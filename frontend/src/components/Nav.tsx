"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "聊天" },
  { href: "/admin/kb", label: "知识库" },
  { href: "/admin/models", label: "模型" },
  { href: "/admin/usage", label: "用量" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="flex h-12 items-center gap-1 border-b border-neutral-200/80 px-4">
      <span className="mr-3 text-sm font-semibold tracking-wide">Umax RAG</span>
      {LINKS.map(({ href, label }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href}
                className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                  active ? "bg-blue-50 font-medium text-blue-600" : "text-neutral-600 hover:bg-neutral-100"}`}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
