// 主题切换：theme.ts 是唯一事实源（cookie `umax_theme` 读写 + html class 应用；
// SSR 直出 class 靠它，隐私模式 cookie 失效则降级为仅本次会话可切换）；
// ThemeToggle 三按钮点击后 html class 与 cookie 同步、当前项 aria-pressed。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { applyTheme, getStoredTheme, storeTheme, THEME_COOKIE } from "@/lib/theme";
import { ThemeToggle } from "@/components/ThemeToggle";

it("applyTheme maps theme to html class (light = no class)", () => {
  applyTheme("dark");
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(document.documentElement.classList.contains("sepia")).toBe(false);
  applyTheme("sepia");
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(document.documentElement.classList.contains("sepia")).toBe(true);
  applyTheme("light");
  expect(document.documentElement.className).toBe("");
});

it("storeTheme/getStoredTheme round-trip the cookie; garbage falls back to null", () => {
  storeTheme("sepia");
  expect(getStoredTheme()).toBe("sepia");
  document.cookie = `${THEME_COOKIE}=neon; path=/`;
  expect(getStoredTheme()).toBeNull();
  document.cookie = `${THEME_COOKIE}=; path=/; max-age=0`;
  expect(getStoredTheme()).toBeNull();
});

it("ThemeToggle click applies class + persists, marks current with aria-pressed", async () => {
  document.cookie = `${THEME_COOKIE}=; path=/; max-age=0`;
  applyTheme("light");
  render(<ThemeToggle />);
  await userEvent.click(screen.getByRole("button", { name: "深色" }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(getStoredTheme()).toBe("dark");
  expect(screen.getByRole("button", { name: "深色" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "浅色" })).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(screen.getByRole("button", { name: "护眼" }));
  expect(document.documentElement.classList.contains("sepia")).toBe(true);
  expect(getStoredTheme()).toBe("sepia");
});
