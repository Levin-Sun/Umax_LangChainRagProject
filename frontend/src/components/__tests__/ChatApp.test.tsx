import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import ChatApp from "@/components/ChatApp";
import { fail, fakeApi, ok } from "@/lib/testkit";
import { P } from "@/lib/paths";
import type { ChatOut, ConversationOut, MessageOut } from "@/lib/types";

const convs: ConversationOut[] = [{ id: 1, title: "退货政策", kb_ids: [] }];
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
    : url === P.convMessages ? ok(history) : undefined),
  POST: async () => ok(chatOut),
});

it("renders history and opens cite drawer without extra request", async () => {
  render(<ChatApp api={api} />);
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
      : url === P.convMessages ? ok(history) : undefined),
    POST: async (url, init) => {
      asked.push((init as { body: { question: string } }).body.question);
      return ok(chatOut);
    },
  });
  render(<ChatApp api={api2} />);
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
  render(<ChatApp api={api3} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("知识库不存在");
});

// 任务7欠账①回归：点击当前已打开会话 = no-op，本地追加的答案不能被 setTurns([]) 抹掉
it("re-clicking the currently open conversation keeps the local answer on screen", async () => {
  const api4 = fakeApi({
    GET: async (url) => (url === P.conversations ? ok(convs)
      : url === P.convMessages ? ok([] as MessageOut[]) : undefined),
    POST: async () => ok(chatOut),
  });
  render(<ChatApp api={api4} />);
  await userEvent.type(screen.getByLabelText("提问"), "售后多久响应？");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  // send 返回 conversation_id=1 → convId=1，与侧栏"退货政策"同 id
  expect(await screen.findByText(/需24小时响应/)).toBeInTheDocument();
  await userEvent.click(await screen.findByText("退货政策"));
  expect(screen.getByText(/需24小时响应/)).toBeInTheDocument(); // 修复前此处查不到（turns 被清空）
});
