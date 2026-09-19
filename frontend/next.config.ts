import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@umax/sdk-ts"],
  // Turbopack 不跟随 file: 依赖的符号链接（"Can't resolve '@umax/sdk-ts'"，webpack 正常，
  // 任务 4 next build --turbopack 实测）。root 扩到仓库根 + resolveAlias 直指真实源文件，
  // 否则 aliased 文件因位于 [project]/ 之外仍报 not found。webpack/turbopack 双兼容。
  turbopack: {
    root: path.resolve(__dirname, ".."),
    resolveAlias: { "@umax/sdk-ts": "../sdk-ts/src/client.ts" },
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: "http://127.0.0.1:8000/api/v1/:path*" }];
  },
};

export default nextConfig;
