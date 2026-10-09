import { expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
// 注释里提到 next/font/google 是解释、不是用法——守卫只判代码（本文件第一次跑就被自己的注释绊倒）
const code = (p: string) => src(p).split("\n").map((l) => l.split("//")[0]).join("\n");

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

// 构建期不许依赖外网取字体。真机踩中（2026-10-09）：next/font/google 在 build 时访问
// fonts.googleapis.com，docker 构建沙箱里被 reset（SSL unexpected eof）——
// `docker compose up -d --build` 直接失败，而同一个 Docker 里跑普通容器却能通，
// 只有构建期这条路走不通。改成自托管后构建离线可完成。
it("字体自托管：layout 不用 next/font/google，且本地字体文件与许可证在位", () => {
  const layout = code("app/layout.tsx");
  expect(layout).not.toContain("next/font/google");
  expect(layout).toContain("next/font/local");
  expect(layout).toContain("Inter-latin-var.woff2");
  const fonts = join(__dirname, "..", "app", "fonts");
  expect(existsSync(join(fonts, "Inter-latin-var.woff2"))).toBe(true);
  expect(existsSync(join(fonts, "LICENSE-Inter.txt"))).toBe(true);   // OFL 要求随字体一起分发
});
