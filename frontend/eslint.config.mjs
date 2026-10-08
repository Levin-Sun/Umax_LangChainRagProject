import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
    ],
  },
  {
    rules: {
      // 有意的例外：本站所有图片都是 data URL / 用户消息里的 base64（白标 logo、聊天附图），
      // next/image 要 loader 与域名白名单，对 data URL 不适用。把这个决定写在配置里一处，
      // 好过在 5 个 <img> 上各来一行 disable（散落的 disable 会慢慢变成没人敢删的噪声）。
      "@next/next/no-img-element": "off",
      // 下划线前缀 = 有意不使用（通行约定）：mock 的形参常常不被函数体使用，
      // 但它是"调用形状"的一部分（有的测试还要读 mock.calls[0][1]），不能删。
      "@typescript-eslint/no-unused-vars": ["warn", {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
      }],
    },
  },
];

export default eslintConfig;
