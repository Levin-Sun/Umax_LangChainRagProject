import "@testing-library/jest-dom/vitest";
// globals:false 下 RTL 无法注册自动 cleanup（依赖全局 afterEach）——同文件多用例
// render 会叠加 DOM（任务 4 ChatApp 实测 getLabelText 命中两份"提问"）。显式挂 cleanup。
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
