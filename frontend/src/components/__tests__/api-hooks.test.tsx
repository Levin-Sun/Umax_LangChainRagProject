import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { call, errText } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync, usePolling } from "@/lib/hooks";
import { fakeApi, ok } from "@/lib/testkit";

describe("errText", () => {
  it("maps ErrorOut.detail / 422 array / network failure", () => {
    expect(errText({ detail: "知识库不存在" })).toBe("知识库不存在");
    expect(errText({ detail: [{ loc: ["body"] }] })).toContain("字段校验");
    expect(errText(undefined)).toContain("网络");
  });
});

function Probe({ fn }: { fn: () => Promise<string> }) {
  const { data, loading, reload } = useAsync(fn);
  return <button onClick={reload}>{loading ? "loading" : String(data)}</button>;
}

it("useAsync resolves and reload re-runs", async () => {
  let n = 0;
  render(<Probe fn={async () => `v${++n}`} />);
  expect(screen.getByText("loading")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("v1")).toBeInTheDocument());
  await userEvent.click(screen.getByText("v1"));
  await waitFor(() => expect(screen.getByText("v2")).toBeInTheDocument());
});

function Poller() {
  const { data } = usePolling(async () => ++counter, { intervalMs: 1000, stopWhen: (v) => v >= 3, enabled: true });
  return <p>{String(data)}</p>;
}
let counter = 0;

it("usePolling stops at terminal value", async () => {
  vi.useFakeTimers();
  render(<Poller />);
  await vi.advanceTimersByTimeAsync(0);   // flush first (immediate) fetch
  expect(screen.getByText("1")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(screen.getByText("2")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(1000);
  expect(screen.getByText("3")).toBeInTheDocument();
  counter = 99;                            // 若未停止，下一轮会显示 99
  await vi.advanceTimersByTimeAsync(3000);
  expect(screen.getByText("3")).toBeInTheDocument();
  vi.useRealTimers();
});

it("fakeApi routes through call() unwrapping", async () => {
  const api = fakeApi({ GET: async (url: string) => (url === P.kb ? ok([{ id: 1 }]) : undefined) });
  await expect(call(api.GET(P.kb))).resolves.toEqual([{ id: 1 }]);
});
