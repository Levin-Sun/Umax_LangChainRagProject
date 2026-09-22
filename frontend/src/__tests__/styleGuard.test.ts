// UI 规范守护：全站只允许五级字阶（text-h1/h2/h3/body/caption）与 400/500/600 字重。
// 出现 Tailwind 默认字号、任意值字号、leading-*、700+ 字重即违规——字号/行高只能来自 @theme 注册的五级。
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

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
