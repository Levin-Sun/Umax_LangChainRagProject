import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");

// @theme 把 --font-sans: var(--font-inter) 展开在 :root（html）处求值。
// next/font 的 variable 类若挂在 <body>，html 上 --font-inter 尚未定义，
// 整条字体链失效——真机实测 body 计算字体回落到系统栈（2026-09-23 踩中）。
it("Inter variable class 必须挂在 <html>，使 :root 处 --font-inter 可用", () => {
  const layout = src("app/layout.tsx");
  const htmlTag = /<html\s[^>]*>/g.exec(layout)?.[0] ?? "";
  const bodyTag = /<body\s[^>]*>/g.exec(layout)?.[0] ?? "";
  expect(htmlTag).toContain("inter.variable");
  expect(bodyTag).not.toContain("inter.variable");
});

it("--font-sans 在 @theme 中引用 --font-inter，mono 直写 Consolas（不依赖 var 链）", () => {
  const globals = src("app/globals.css");
  const theme = globals.match(/@theme[^{]*\{[\s\S]*?\n\}/g)?.join("\n") ?? "";
  expect(theme).toContain("--font-sans: var(--font-inter)");
  expect(theme).toMatch(/--font-mono:\s*Consolas/);
});
