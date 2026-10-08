// UI 规范守护：全站只允许五级字阶（text-h1/h2/h3/body/caption）与 400/500/600 字重。
// 出现 Tailwind 默认字号、任意值字号、leading-*、700+ 字重即违规——字号/行高只能来自 @theme 注册的五级。
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const RULES: Array<[string, RegExp]> = [
  ["默认字号（改用 text-h1/h2/h3/body/caption）", /(?<![\w-])text-(?:xs|sm|base|lg|xl|[2-9]xl)(?![\w-])/g],
  ["任意值字号（禁自定义字号）", /(?<![\w-])text-\[/g],
  ["手动行高（行高随五级字阶锁定）", /(?<![\w-])leading-[\w[]/g],
  ["700+ 字重（仅允许 400/500/600）", /(?<![\w-])font-(?:bold|extrabold|black)(?![\w-])/g],
];

function collecttsx(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "__tests__" ? [] : collecttsx(p);
    return p.endsWith(".tsx") ? [p] : [];
  });
}

describe("style guard", () => {
  it("src 组件中不存在规范外字号/行高/字重", () => {
    const root = path.resolve(__dirname, "..");
    const violations: string[] = [];
    for (const file of collecttsx(root)) {
      const lines = fs.readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        for (const [label, re] of RULES) {
          for (const m of line.matchAll(re)) {
            violations.push(`${path.relative(root, file)}:${i + 1} ${label} → ${m[0]}`);
          }
        }
      });
    }
    expect(violations).toEqual([]);
  });
});

// 溢出守护（真机踩过）：表格必须包在 overflow-x-auto 容器里。
// 起因：知识库页抽屉是固定宽兄弟列，窄屏下 auto 表格布局的最小内容宽度撑破所在列，
// 内容直接画到抽屉上（文字重叠、列挤在一起）。表格能在自己盒子里横向滚动就不会外溢。
it("每个 table 都包在 overflow-x-auto 容器中", () => {
  const root = path.resolve(__dirname, "..");
  const violations: string[] = [];
  for (const file of collecttsx(root)) {
    const src = fs.readFileSync(file, "utf8");
    for (const m of src.matchAll(/<table/g)) {
      const before = src.slice(0, m.index);
      const lastDiv = before.lastIndexOf("<div");
      const tag = lastDiv >= 0 ? before.slice(lastDiv, before.indexOf(">", lastDiv) + 1) : "";
      if (!tag.includes("overflow-x-auto")) {
        const line = before.split("\n").length;
        violations.push(`${path.relative(root, file)}:${line} 表格缺 overflow-x-auto 容器`);
      }
    }
  }
  expect(violations).toEqual([]);
});

// JSX 文本里的裸引号 / Markdown 星号（真机踩过四次引号、两次星号）：
// 它们在浏览器里原样显示（`**不并入通过率**`、`回答"谁..."`），而 eslint 的
// react/no-unescaped-entities 只在 build 那一步才拦，星号更是根本没人拦。
//
// 用 TS 编译器 API 精确取 JsxText 节点：正则版会把箭头函数（`=> rej(new Error("x"))`）
// 和属性值（`title="..."`，那里引号是合法的）一起误报——"只有真 JSX 文本才算"这件事
// 编译器比正则清楚。这类"写的时候看不出、只在页面上丑"的问题靠人记住是记不住的（已连踩四次）。
it("JSX 文本里不出现裸引号或 Markdown 星号", () => {
  const root = path.resolve(__dirname, "..");
  const violations: string[] = [];
  for (const file of collecttsx(root)) {
    const src = fs.readFileSync(file, "utf8");
    const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = (node: ts.Node): void => {
      if (ts.isJsxText(node)) {
        const text = node.getText(sf);
        if (/["*]/.test(text)) {
          const at = sf.getLineAndCharacterOfPosition(node.getStart(sf));
          violations.push(`${path.relative(root, file)}:${at.line + 1} → ${text.trim().slice(0, 60)}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  expect(violations).toEqual([]);
});
