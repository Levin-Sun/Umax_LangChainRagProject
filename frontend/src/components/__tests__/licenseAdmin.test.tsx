// 授权状态页：有效态显客户/到期/剩余天数；失效态显原因与只读说明；指纹可复制。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LicenseAdmin from "@/components/LicenseAdmin";
import { P } from "@/lib/paths";
import { fakeApi, ok } from "@/lib/testkit";
import { describe, expect, it, vi } from "vitest";

const base = { license_key: "UMX-ABC123", customer: "星辰科技",
  issued_at: "2026-10-07T00:00:00+00:00", expires_at: "2027-10-07T00:00:00+00:00",
  features: { max_docs: 5000 }, machine_fingerprint: "fp-delivery-001" };

const renderPage = (me: object) =>
  render(<LicenseAdmin api={fakeApi({ GET: (u) => (u === P.license ? ok(me) : ok([])) })} />);

describe("授权状态页", () => {
  it("有效：显客户/编号/剩余天数与机器指纹", async () => {
    renderPage({ ...base, enforced: true, valid: true, reason: null, days_left: 365 });
    expect(await screen.findByText("星辰科技")).toBeInTheDocument();
    expect(screen.getByText("UMX-ABC123")).toBeInTheDocument();
    expect(screen.getByText("365 天")).toBeInTheDocument();
    expect(screen.getByText("有效")).toBeInTheDocument();
    expect(screen.getByText("fp-delivery-001")).toBeInTheDocument();
  });

  it("到期：显原因与只读说明，标不可用", async () => {
    renderPage({ ...base, enforced: true, valid: false, days_left: -30,
                 reason: "授权已于 2026-09-07 到期，请联系服务商续期" });
    expect(await screen.findByText("不可用")).toBeInTheDocument();
    expect(screen.getByText(/到期，请联系服务商续期/)).toBeInTheDocument();
    expect(screen.getByText(/系统为只读/)).toBeInTheDocument();
  });

  it("开发模式：标开发模式并给提示", async () => {
    renderPage({ ...base, enforced: false, valid: true, days_left: null,
                 reason: "未启用授权校验（开发模式）", customer: null, license_key: null,
                 issued_at: null, expires_at: null, features: {} });
    expect(await screen.findByText("开发模式")).toBeInTheDocument();
    expect(screen.getByText("未启用授权校验（开发模式）")).toBeInTheDocument();
  });

  it("临期（≤30 天）提示及时续期", async () => {
    renderPage({ ...base, enforced: true, valid: true, reason: null, days_left: 12 });
    expect(await screen.findByText("即将到期，请及时续期")).toBeInTheDocument();
  });

  it("复制指纹调用剪贴板", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    renderPage({ ...base, enforced: true, valid: true, reason: null, days_left: 365 });
    await userEvent.click(await screen.findByRole("button", { name: "复制指纹" }));
    expect(writeText).toHaveBeenCalledWith("fp-delivery-001");
    expect(await screen.findByRole("button", { name: "已复制" })).toBeInTheDocument();
  });
});
