import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AnswerBody } from "@/components/AnswerBody";
import type { Citation } from "@/lib/types";

// 真机反馈：换成 DeepSeek 之后回答快很多，但 **星号**、`反引号`、短横线列表原样显示，
// 整段糊在一起、可读性很差。这里把"安全子集"的渲染行为钉住：
// 排版要出来，原始符号不能露，模型输出里的 HTML 只能当文字。
const cite = (n: number, doc: string): Citation =>
  ({ n, doc_name: doc, chunk_id: n, excerpt: "节选" });

it("渲染 Markdown 子集：标题/粗体/行内代码/列表都不再是原始符号", () => {
  const md = "### 退货时效\n\n- **普通商品**：7 日内 `无理由`\n- 生鲜：24 小时\n\n1. 申请\n2. 审核";
  render(<AnswerBody text={md} citations={[]} onCite={() => {}} />);
  expect(screen.getByRole("heading", { name: "退货时效" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(4);
  expect(screen.getByText("普通商品").tagName).toBe("STRONG");
  expect(screen.getByText("无理由").tagName).toBe("CODE");
  expect(screen.queryByText(/\*\*/)).toBeNull();      // 不许露出星号
  expect(screen.queryByText(/`/)).toBeNull();         // 不许露出反引号
});

it("紧邻同源角标合并成 1-3，不同来源不合并", () => {
  const same = [cite(1, "退货规则.txt"), cite(2, "退货规则.txt"), cite(3, "退货规则.txt")];
  const first = render(<AnswerBody text="规则如下[1][2][3]。" citations={same} onCite={() => {}} />);
  expect(screen.getByRole("button", { name: "引用 1-3：退货规则.txt" })).toHaveTextContent("1-3");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  first.unmount();

  render(<AnswerBody text="见[1][2]。" citations={[cite(1, "A.txt"), cite(2, "B.txt")]} onCite={() => {}} />);
  expect(screen.getByRole("button", { name: "引用 1：A.txt" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "引用 2：B.txt" })).toBeInTheDocument();
});

it("点角标回传引用对象（右侧原文抽屉照旧可用）", async () => {
  const onCite = vi.fn();
  render(<AnswerBody text="答案[1]。" citations={[cite(1, "手册.md")]} onCite={onCite} />);
  await userEvent.click(screen.getByRole("button", { name: "引用 1：手册.md" }));
  expect(onCite).toHaveBeenCalledWith(expect.objectContaining({ n: 1, doc_name: "手册.md" }));
});

it("段内换行保留：模型用单换行分行时不会糊成一坨", () => {
  render(<AnswerBody text={"第一行\n第二行"} citations={[]} onCite={() => {}} />);
  expect(screen.getByText(/第一行/).textContent).toContain("\n");
});

it("模型输出里夹 HTML 只当文字（不解析成标签）", () => {
  render(<AnswerBody text={'<img src=x onerror="alert(1)"> 与 <b>粗</b>'} citations={[]} onCite={() => {}} />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText(/onerror/)).toBeInTheDocument();
});

it("角标序号在引用里找不到时原样保留，不吞内容", () => {
  render(<AnswerBody text="见[9]。" citations={[cite(1, "手册.md")]} onCite={() => {}} />);
  expect(screen.getByText(/\[9\]/)).toBeInTheDocument();
});
