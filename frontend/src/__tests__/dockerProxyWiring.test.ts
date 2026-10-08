import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(__dirname, "..", "..");            // frontend/
const read = (p: string) => readFileSync(join(root, p), "utf8");
const compose = () => readFileSync(join(root, "..", "docker-compose.yml"), "utf8");

// 交付链路真机踩中（2026-10-09，新机器第一次跑 compose 全链就卡在"页面登录检测"）：
// next.config 的 rewrites() 在**构建期**求值一次、烧进 .next/routes-manifest.json，
// standalone 的 server.js 起来只读这份清单——运行期环境变量改不动它。
// 当时 frontend/Dockerfile 只在**运行阶段**设了 BACKEND_ORIGIN，构建层没给，
// 于是产物里留的是开发缺省 http://127.0.0.1:8000（在容器里那是前端自己），
// /api/v1/* 全部 ECONNREFUSED，登录页拉 /auth/me 与 /branding 双双失败 → Internal Server Error。
// 更阴的是它骗过了自检：smoke_delivery.py 打的是后端 :8000，压根不经过前端代理，12/12 全绿。
// 下面把「构建层必须拿到 BACKEND_ORIGIN 且与运行层同源」钉成用例——摘掉 ARG/args 时 CI 先红。

it("构建层必须拿到 BACKEND_ORIGIN，且在 npm run build 之前", () => {
  const stages = read("Dockerfile").split(/^FROM /m).slice(1);
  expect(stages).toHaveLength(2);
  const buildStage = stages[0];
  const argAt = buildStage.indexOf("ARG BACKEND_ORIGIN=");
  const buildAt = buildStage.indexOf("npm run build");
  expect(argAt).toBeGreaterThan(-1);
  expect(buildAt).toBeGreaterThan(-1);
  expect(argAt).toBeLessThan(buildAt);                 // 顺序即语义：晚了就烧不到产物里
  expect(buildStage).toContain("ENV BACKEND_ORIGIN=${BACKEND_ORIGIN}");
});

it("运行层与构建层同源（都引同一个 ARG，不写死字面量）", () => {
  const dockerfile = read("Dockerfile");
  for (const stage of dockerfile.split(/^FROM /m).slice(1)) {
    expect(stage).toContain("ARG BACKEND_ORIGIN=http://backend:8000");
  }
  expect(dockerfile).toContain("BACKEND_ORIGIN=${BACKEND_ORIGIN}");
});

it("compose 把 BACKEND_ORIGIN 传进构建参数（不传就退回开发缺省）", () => {
  expect(compose()).toContain("BACKEND_ORIGIN: ${BACKEND_ORIGIN:-http://backend:8000}");
});

it("next.config 的 rewrite 目标取自 BACKEND_ORIGIN", () => {
  expect(read("next.config.ts")).toContain('process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000"');
});
