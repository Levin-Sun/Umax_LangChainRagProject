import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@umax/sdk-ts"],
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: "http://127.0.0.1:8000/api/v1/:path*" }];
  },
};

export default nextConfig;
