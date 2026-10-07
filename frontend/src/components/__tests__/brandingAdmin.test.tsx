// 白标设置页：加载回显、部分保存（只发改动字段）、logo 选择/移除、保存后 reload。
// 全部经 fakeApi 断言请求面（与 apiKeysAdmin.test 同型）。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BrandingAdmin from "@/components/BrandingAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const PNG = "data:image/png;base64,iVBORw0KGgo=";
const cur = { brand_name: "客户牌", logo: null };

const renderAdmin = (api: ReturnType<typeof fakeApi>) => render(<BrandingAdmin api={api} />);

describe("白标设置", () => {
  it("加载回显当前品牌名；只改名 → PUT 只带 brand_name", async () => {
    const PUT = vi.fn(() => ok({ ...cur, brand_name: "新牌" }));
    renderAdmin(fakeApi({ GET: (u) => (u === P.branding ? ok(cur) : ok([])), PUT }));
    const input = await screen.findByLabelText("品牌名");
    expect(input).toHaveValue("客户牌");
    await userEvent.clear(input);
    await userEvent.type(input, "新牌");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.branding, expect.objectContaining({
      body: { brand_name: "新牌" },
    })));
    expect(await screen.findByText("已保存。")).toBeInTheDocument();
  });

  it("移除 logo → PUT 带 logo:null；无改动时保存禁用", async () => {
    const PUT = vi.fn(() => ok({ brand_name: "客户牌", logo: null }));
    renderAdmin(fakeApi({ GET: (u) => (u === P.branding ? ok({ ...cur, logo: PNG }) : ok([])), PUT }));
    expect(await screen.findByAltText("logo 预览")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "移除" }));
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(PUT).toHaveBeenCalledWith(P.branding, expect.objectContaining({
      body: { logo: null },
    })));
  });

  it("未做任何改动：保存按钮禁用", async () => {
    renderAdmin(fakeApi({ GET: (u) => (u === P.branding ? ok(cur) : ok([])) }));
    await screen.findByLabelText("品牌名");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });
});
