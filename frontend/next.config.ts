import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@umax/sdk-ts"],
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
    // rewrite 在服务端执行：容器部署经 compose 网络打 backend 服务（BACKEND_ORIGIN），
    // 本机开发缺省 127.0.0.1:8000 不变
    const origin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
    return [{ source: "/api/v1/:path*", destination: `${origin}/api/v1/:path*` }];
  },
};

export default nextConfig;
