import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/dom";
import ChatApp from "@/components/ChatApp";
import { AuthProvider } from "@/lib/auth";
import { fail, fakeApi, ok } from "@/lib/testkit";
import { P } from "@/lib/paths";
import type { AuthMe, ChatOut, ConversationOut, MessageOut } from "@/lib/types";

// ChatApp 换轨 useAuth：me 就绪前不拉业务数据，未登录弹回登录页
const nav = vi.hoisted(() => ({ replaced: [] as string[] }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: () => {}, replace: (to: string) => { nav.replaced.push(to); } }),
}));
const ME: AuthMe = { email: "a@x.com", name: "阿管", role: "admin", kb_ids: null, must_change_password: false };
// 包一层：/auth/me 恒 200（admin），其余透传给业务假 api
type LooseCall = (u: string, init?: unknown) => unknown;
const withAuth = (biz: typeof api) => fakeApi({
  GET: (u) => (u.includes("/auth/me") ? ok(ME) : (biz.GET as unknown as LooseCall)(u)),
  POST: (u, init) => (biz.POST as unknown as LooseCall)(u, init),
});


const convs: ConversationOut[] = [{ id: 1, title: "退货政策", kb_ids: [] }];
const QUOTA = { daily_used: 0, daily_limit: null, monthly_used: 0, monthly_limit: null,
                near_limit: false, exceeded: false, warn_ratio: 0.8 };
const history: MessageOut[] = [
  { id: 1, role: "user", content: [{ type: "text", text: "退货几天？" }], citations: null },
  { id: 2, role: "assistant", content: [{ type: "text", text: "据资料，退货需7天响应[1]。" }],
    citations: [{ n: 1, doc_name: "运维手册.md", chunk_id: 9, excerpt: "退货窗口为 7 个自然日" }] },
];
const chatOut: ChatOut = {
  conversation_id: 1, answer: "需24小时响应[1]。",
  citations: [{ n: 1, doc_name: "运营手册.txt", chunk_id: 42, excerpt: "售后响应承诺：24小时" }],
  cited_docs: ["运营手册.txt"], usage: { prompt_tokens: 100, completion_tokens: 20 },
};

const api = fakeApi({
  GET: async (url) => (url === P.conversations ? ok(convs)
    : url === P.convMessages ? ok(history) : url === P.usageMe ? ok(QUOTA) : undefined),
  POST: async () => ok(chatOut),
});

it("renders history and opens cite drawer without extra request", async () => {
  const full = withAuth(api);
  render(<AuthProvider client={full}><ChatApp api={full} /></AuthProvider>);
  // 计划笔误修正（同 Ruling-T5 类）：render() 与 getByText 之间无 await，effect 里
  // 发起的会话列表 GET 微任务永不冲刷——同步 getByText 结构上不可能命中。
  // 改为 findByText（存在性断言强度不变，仅换异步查询），expect 断言全部逐字保留。
  await userEvent.click(await screen.findByText("退货政策"));
  expect(await screen.findByText(/退货需7天响应/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /\[1\] 运维手册\.md/ }));
  expect(await screen.findByText("退货窗口为 7 个自然日")).toBeInTheDocument();
});

it("asks question, shows inline citation chip, allows second question", async () => {
  const asked: unknown[] = [];
  const api2 = fakeApi({
    GET: async (url) => (url === P.conversations ? ok(convs)
      : url === P.convMessages ? ok(history) : url === P.usageMe ? ok(QUOTA) : undefined),
    POST: async (url, init) => {
      asked.push((init as { body: { question: string } }).body.question);
      return ok(chatOut);
    },
  });
  render(<AuthProvider client={withAuth(api2)}><ChatApp api={withAuth(api2)} /></AuthProvider>);
  await screen.findByLabelText("提问"); // me 就绪后骨架让位于聊天界面
  await userEvent.type(screen.getByLabelText("提问"), "售后多久响应？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  const chip = await screen.findByRole("button", { name: /\[1\] 运营手册\.txt/ });
  await userEvent.click(chip);
  expect(await screen.findByText("售后响应承诺：24小时")).toBeInTheDocument();
  expect(asked).toEqual(["售后多久响应？"]);
  // 第二条可继续提问（发送锁必须释放）
  await userEvent.type(screen.getByLabelText("提问"), "换货呢？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(asked).toEqual(["售后多久响应？", "换货呢？"]);
});

it("renders backend detail in banner when list fails", async () => {
  const api3 = fakeApi({ GET: async () => fail("知识库不存在", 404) });
  const full = withAuth(api3);
  render(<AuthProvider client={full}><ChatApp api={full} /></AuthProvider>);
  expect(await screen.findByRole("alert")).toHaveTextContent("知识库不存在");
});

// 交互修复回归：提问必须点击发送后立即上屏，答案到达后再追加（而非等 POST 一起渲染）。
// 断言限定在 role=log 的消息区——jsdom 里 textarea 的 value 即其 textContent，
// 全局 getByText 会误匹配输入框（首版测试因此假绿）。
it("shows the question immediately while the answer is still pending", async () => {
  let resolvePost: ((v: unknown) => void) | undefined;
  const api5 = fakeApi({
    GET: async (url) => (url === P.conversations ? ok(convs)
      : url === P.convMessages ? ok([] as MessageOut[]) : url === P.usageMe ? ok(QUOTA) : undefined),
    POST: () => new Promise((r) => { resolvePost = r; }),
  });
  const full5 = withAuth(api5);
  render(<AuthProvider client={full5}><ChatApp api={full5} /></AuthProvider>);
  await screen.findByLabelText("提问");
  await userEvent.type(screen.getByLabelText("提问"), "售后多久响应？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  const log = screen.getByRole("log");
  expect(within(log).getByText("售后多久响应？")).toBeInTheDocument();     // 答案未回，提问已在
  expect(within(log).queryByText(/需24小时响应/)).not.toBeInTheDocument();
  await act(async () => { resolvePost?.(ok(chatOut)); });
  expect(await within(log).findByText(/需24小时响应/)).toBeInTheDocument();
  expect(within(log).getAllByText("售后多久响应？")).toHaveLength(1);      // 无重复气泡
});

it("removes the pending question and shows banner when send fails", async () => {
  const api6 = fakeApi({
    GET: async (url) => (url === P.conversations ? ok(convs)
      : url === P.convMessages ? ok([] as MessageOut[]) : url === P.usageMe ? ok(QUOTA) : undefined),
    POST: async () => fail("模型不可用", 503),
  });
  const full6 = withAuth(api6);
  render(<AuthProvider client={full6}><ChatApp api={full6} /></AuthProvider>);
  await screen.findByLabelText("提问");
  await userEvent.type(screen.getByLabelText("提问"), "会炸吗？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("模型不可用");
  expect(within(screen.getByRole("log")).queryByText("会炸吗？")).not.toBeInTheDocument(); // 失败不留孤儿气泡
});
it("re-clicking the currently open conversation keeps the local answer on screen", async () => {
  const api4 = fakeApi({
    GET: async (url) => (url === P.conversations ? ok(convs)
      : url === P.convMessages ? ok([] as MessageOut[]) : url === P.usageMe ? ok(QUOTA) : undefined),
    POST: async () => ok(chatOut),
  });
  const full4 = withAuth(api4);
  render(<AuthProvider client={full4}><ChatApp api={full4} /></AuthProvider>);
  await screen.findByLabelText("提问");
  await userEvent.type(screen.getByLabelText("提问"), "售后多久响应？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  // send 返回 conversation_id=1 → convId=1，与侧栏"退货政策"同 id
  expect(await screen.findByText(/需24小时响应/)).toBeInTheDocument();
  await userEvent.click(await screen.findByText("退货政策"));
  expect(screen.getByText(/需24小时响应/)).toBeInTheDocument(); // 修复前此处查不到（turns 被清空）
});

// 任务 7 换轨：me 就绪前只有骨架，业务请求一次不发；loaded 无 me → 弹回登录页
it("unauthenticated chat page bounces to login and never fires business GETs", async () => {
  nav.replaced = [];
  const bizGet = vi.fn(async (_u: string) => ok(convs));
  const anon = fakeApi({
    GET: (u: string) => (u.includes("/auth/me") ? fail("需要登录", 401) : bizGet(u)),
    POST: async () => ok(chatOut),
  });
  render(<AuthProvider client={anon}><ChatApp api={anon} /></AuthProvider>);
  await waitFor(() => expect(nav.replaced).toEqual(["/admin/login"]));
  expect(bizGet).not.toHaveBeenCalled();
  expect(screen.queryByRole("log")).not.toBeInTheDocument();
});

it("renders skeleton while me is still in flight (no premature business GET)", () => {
  const pending = { GET: (u: string) => (u.includes("/auth/me") ? new Promise(() => {}) : ok(convs)),
                    POST: () => ok(undefined) } as never;
  render(<AuthProvider client={pending}><ChatApp api={pending} /></AuthProvider>);
  expect(screen.getByText("加载中…")).toBeInTheDocument();
  expect(screen.queryByRole("log")).not.toBeInTheDocument();
});

// ---- 会话删除（体验反馈④）：悬停 ✕ → DELETE，删除当前会话回"新建会话"态，列表移除 ----
it("deletes a conversation via DELETE and clears it from the list", async () => {
  const DELETE = vi.fn(() => { listed.length = 0; return ok(undefined); });   // 有状态：删后列表空
  const listed: ConversationOut[] = [...convs];
  const full = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? ok(ME)
      : u === P.conversations ? ok(listed)
      : u === P.convMessages ? ok(history) : u === P.usageMe ? ok(QUOTA) : undefined),
    DELETE,
  });
  render(<AuthProvider client={full as never}><ChatApp api={full as never} /></AuthProvider>);
  const item = await screen.findByText("退货政策");
  await userEvent.hover(item);
  await userEvent.click(screen.getByRole("button", { name: "删除会话 退货政策" }));
  await waitFor(() => expect(DELETE).toHaveBeenCalledWith(P.conversation, expect.objectContaining({
    params: { path: { conv_id: 1 } },
  })));
  await waitFor(() => expect(screen.queryByText("退货政策")).not.toBeInTheDocument());
});

// ---- 会话搜索 + 传图提问（阶段 2 放开）----
it("搜索框输入 → GET /conversations 带 q 参数", async () => {
  const GET = vi.fn((u: string, init?: unknown) =>
    u.includes("/auth/me") ? ok(ME) : ok([]));
  render(<AuthProvider client={fakeApi({ GET }) as never}><ChatApp api={fakeApi({ GET }) as never} /></AuthProvider>);
  await screen.findByLabelText("搜索会话");
  await waitFor(() => expect(GET.mock.calls.some(([u]) => String(u).startsWith(P.conversations))).toBe(true));
  await userEvent.type(screen.getByLabelText("搜索会话"), "退货");
  await waitFor(() => {
    // openapi-fetch 把 query 放在 init.params（拦到的是方法签名，非最终 URL）；取最后一次调用
    const call = GET.mock.calls.filter(([u]) => String(u).startsWith(P.conversations)).at(-1);
    expect(((call?.[1] ?? {}) as { params?: { query?: { q?: string } } }).params?.query?.q).toBe("退货");
  });
});

it("附加图片 → 预览条出现 → 发送时 body 带 images", async () => {
  const POST = vi.fn(() => ok({ ...chatOut, conversation_id: 1 }));
  const file = new File(["pngbytes"], "shot.png", { type: "image/png" });
  const full = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? ok(ME)
      : u === P.conversations ? ok([]) : u === P.convMessages ? ok([])
      : u === P.usageMe ? ok(QUOTA) : undefined),
    POST,
  });
  render(<AuthProvider client={full as never}><ChatApp api={full as never} /></AuthProvider>);
  await screen.findByLabelText("提问");   // 等主区渲染完，隐藏 file input 才存在
  // 隐藏的 file input 直取（📎 按钮只是它的开启器）
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, file);
  expect(await screen.findByAltText("待发送图片 1")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("提问"), "图里是什么");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(POST).toHaveBeenCalledWith(P.chat, expect.objectContaining({
    body: expect.objectContaining({ question: "图里是什么", images: [expect.stringMatching(/^data:image\/png;base64,/)] }),
  })));
  // 乐观气泡即含图片
  expect(screen.getAllByAltText("随问图片").length).toBeGreaterThan(0);
});

it("历史消息里的 image part 渲染成图片", async () => {
  const withImg: MessageOut[] = [
    { id: 1, role: "user", content: [{ type: "text", text: "看图" }, { type: "image_url", image_url: { url: "data:image/png;base64,AAA" } }], citations: null },
  ];
  const full = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? ok(ME)
      : u === P.conversations ? ok(convs) : u === P.convMessages ? ok(withImg)
      : u === P.usageMe ? ok(QUOTA) : undefined),
  });
  render(<AuthProvider client={full as never}><ChatApp api={full as never} /></AuthProvider>);
  await screen.findByText("退货政策");
  await userEvent.click(screen.getByText("退货政策"));
  expect(await screen.findByAltText("随问图片")).toHaveAttribute("src", "data:image/png;base64,AAA");
});

// ---- 配额预警条（§C）：接近上限出提示、超限出强提示 ----
it("接近上限显示预警条；超限显示用尽提示", async () => {
  const near = { ...QUOTA, daily_used: 90, daily_limit: 100, near_limit: true };
  const full = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? ok(ME)
      : u === P.conversations ? ok([]) : u === P.convMessages ? ok([])
      : u === P.usageMe ? ok(near) : undefined),
  });
  const { unmount } = render(<AuthProvider client={full as never}><ChatApp api={full as never} /></AuthProvider>);
  expect(await screen.findByRole("status")).toHaveTextContent("token 用量已接近上限");
  unmount();
  const out = { ...QUOTA, daily_used: 100, daily_limit: 100, exceeded: true };
  const full2 = fakeApi({
    GET: (u) => (u.includes("/auth/me") ? ok(ME)
      : u === P.conversations ? ok([]) : u === P.convMessages ? ok([])
      : u === P.usageMe ? ok(out) : undefined),
  });
  render(<AuthProvider client={full2 as never}><ChatApp api={full2 as never} /></AuthProvider>);
  expect(await screen.findByRole("status")).toHaveTextContent("token 配额已用尽");
});
