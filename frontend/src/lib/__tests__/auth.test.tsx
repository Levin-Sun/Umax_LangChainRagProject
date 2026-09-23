// AuthProvider：/auth/me 是登录态唯一真相源（admin_hint cookie 已退役）——
// 挂载拉一次、401 静默降为匿名、login 成功后重拉、logout 清态；
// api.ts 的 401 注入点（setUnauthorizedHandler）只在 401 触发、默认 no-op。
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/lib/auth";
import { call, callVoid, setUnauthorizedHandler } from "@/lib/api";
import { fakeApi, ok, fail } from "@/lib/testkit";

const ME = { email: "a@x.com", name: "a", role: "admin" as const, kb_ids: null };

function Probe({ client }: { client: never }) {
  const { me, loaded, login } = useAuth();
  return (
    <div>
      <span data-testid="state">{loaded ? (me ? me.role : "anon") : "loading"}</span>
      <button onClick={() => { void login("a@x.com", "pw").catch(() => {}); }}>login</button>
    </div>
  );
}

it("挂载即拉 /auth/me：200→admin，401→anon", async () => {
  const api = fakeApi({ GET: (u) => (u.includes("/auth/me") ? ok(ME) : ok([])) }) as never;
  render(<AuthProvider client={api}><Probe client={api} /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("admin"));
});

it("me 401 → 匿名态不抛错；login 成功后重拉", async () => {
  type MeResult = { data?: unknown; error?: unknown; response?: Response };
  let me: Promise<MeResult> = fail("需要登录", 401);
  const api = { GET: vi.fn((u: string) => (u.includes("/auth/me") ? me : ok([]))),
               POST: vi.fn(() => { me = ok(ME); return ok(undefined); }) } as never;
  render(<AuthProvider client={api}><Probe client={api} /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("anon"));
  await act(async () => { await userEvent.click(screen.getByText("login")); });
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("admin"));
});

it("login 走 P.authLogin（email+password），me 仍 401 时抛错不留假登录态", async () => {
  const post = vi.fn(() => fail("邮箱或口令错误", 401));
  const api = fakeApi({ GET: () => fail("需要登录", 401), POST: post });
  render(<AuthProvider client={api}><Probe client={api as never} /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("anon"));
  await act(async () => { await userEvent.click(screen.getByText("login")); });
  expect(post).toHaveBeenCalledWith("/api/v1/auth/login", expect.objectContaining({
    body: { email: "a@x.com", password: "pw" },
  }));
  await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("anon"));
});

it("logout 走 P.authLogout 后 me 清空", async () => {
  let logged = true;
  const post = vi.fn(() => { logged = false; return ok(undefined); });
  const api = fakeApi({
    GET: () => (logged ? ok(ME) : fail("需要登录", 401)),
    POST: post,
  });
  function Out() {
    const { me, loaded, logout } = useAuth();
    return <button data-testid="out" disabled={!loaded} onClick={() => void logout().catch(() => {})}>
      {me ? "in" : "out"}
    </button>;
  }
  render(<AuthProvider client={api}><Out /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("in"));
  await userEvent.click(screen.getByTestId("out"));
  await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("out"));
  expect(post).toHaveBeenCalledWith("/api/v1/auth/logout", expect.anything());
});

it("useAuth 在 Provider 外抛错", () => {
  function Loose() { useAuth(); return null; }
  vi.spyOn(console, "error").mockImplementation(() => {}); // 预期内的渲染抛错不打堆栈噪音
  expect(() => render(<Loose />)).toThrow("useAuth 必须在 AuthProvider 内");
  vi.mocked(console.error).mockRestore();
});

it("call/callVoid 捕获 401 时先触发注入钩子再抛；非 401 不触发", async () => {
  const h = vi.fn();
  setUnauthorizedHandler(h);
  try {
    await expect(call(Promise.resolve({
      data: undefined, error: { detail: "需要登录" },
      response: new Response("x", { status: 401 }),
    }))).rejects.toMatchObject({ status: 401 });
    expect(h).toHaveBeenCalledTimes(1);
    await expect(call(Promise.resolve({
      data: undefined, error: { detail: "服务器炸了" },
      response: new Response("x", { status: 500 }),
    }))).rejects.toMatchObject({ status: 500 });
    expect(h).toHaveBeenCalledTimes(1); // 非 401 不触发
    await expect(callVoid(Promise.resolve({
      error: { detail: "需要登录" }, response: new Response("x", { status: 401 }),
    }))).rejects.toBeInstanceOf(Error);
    expect(h).toHaveBeenCalledTimes(2);
    await expect(call(Promise.resolve({ data: 1, response: new Response() }))).resolves.toBe(1);
    expect(h).toHaveBeenCalledTimes(2); // 成功路径不触发
  } finally {
    setUnauthorizedHandler(() => {});
  }
});

afterEach(() => setUnauthorizedHandler(() => {}));
