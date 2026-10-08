import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@umax/sdk-ts"],
  // 构建产物目录可用 NEXT_DIST_DIR 覆盖：dev 服务器与生产构建共用 .next 会互相踩缓存
  // （dev 跑着时执行 next build → dev 的 _buildManifest.js 被覆盖 → 页面 500）。
  // 校验性构建用 NEXT_DIST_DIR=.next-verify，dev 的 .next 不受影响；Docker/正式构建不设此变量。
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // Docker 交付：standalone 产物自带精简 node_modules，运行镜像无需整仓（部署见 docs/DEPLOY.md）
  output: "standalone",
  // Turbopack 不跟随 file: 依赖的符号链接（"Can't resolve '@umax/sdk-ts'"，webpack 正常，
  // 任务 4 next build --turbopack 实测）。root 扩到仓库根 + resolveAlias 直指真实源文件，
  // 否则 aliased 文件因位于 [project]/ 之外仍报 not found。webpack/turbopack 双兼容。
  turbopack: {
    root: path.resolve(__dirname, ".."),
    resolveAlias: { "@umax/sdk-ts": "../sdk-ts/src/client.ts" },
  },
  async rewrites() {
    // 容器部署经 compose 网络打 backend 服务；本机开发缺省 127.0.0.1:8000。
    // ⚠️ rewrites() 在 **构建期** 求值一次，结果写进 .next/routes-manifest.json，
    // 运行期只读这份清单——改 BACKEND_ORIGIN 必须重新 build（docker compose up -d --build），
    // 光重启容器不生效。前端镜像的构建参数由 frontend/Dockerfile 的 ARG 接住，
    // compose 在 frontend.build.args 里传（这条链路由 dockerProxyWiring.test.ts 钉住）。
    const origin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
    return [{ source: "/api/v1/:path*", destination: `${origin}/api/v1/:path*` }];
  },
};

export default nextConfig;
