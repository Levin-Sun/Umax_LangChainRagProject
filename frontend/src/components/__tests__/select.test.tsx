import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Select } from "@/components/ui/select";

// 自绘下拉的契约：展开、选中回填、三种关闭路径、disabled 不展开。
// 为什么值得测：原生 <select> 的展开列表是系统画的、管不到（用户连续两轮反馈"圆角/看不到选项"），
// 换成自绘组件后这些行为就成了我们自己的责任，必须有牙看着。
const opts = [{ value: "a", label: "甲" }, { value: "b", label: "乙" }];

it("展开 → 点选项 → 回填并关闭", async () => {
  const onChange = vi.fn();
  render(<Select label="场景" value="a" options={opts} onChange={onChange} />);
  expect(screen.queryByRole("listbox")).toBeNull();
  await userEvent.click(screen.getByLabelText("场景"));
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("option", { name: "乙" }));
  expect(onChange).toHaveBeenCalledWith("b");
  expect(screen.queryByRole("listbox")).toBeNull();     // 选完即关，不留浮层
});

it("Esc 与点击外部都能关闭", async () => {
  render(<Select label="场景" value="a" options={opts} onChange={() => {}} />);
  const trigger = screen.getByLabelText("场景");
  await userEvent.click(trigger);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("listbox")).toBeNull();
  await userEvent.click(trigger);
  await userEvent.click(document.body);
  expect(screen.queryByRole("listbox")).toBeNull();
});

it("disabled 时不展开", async () => {
  render(<Select label="场景" value="a" options={opts} onChange={() => {}} disabled />);
  await userEvent.click(screen.getByLabelText("场景"));
  expect(screen.queryByRole("listbox")).toBeNull();
});

it("按钮回显的是 label 而不是 value", () => {
  render(<Select label="场景" value="b" options={opts} onChange={() => {}} />);
  expect(screen.getByLabelText("场景")).toHaveTextContent("乙");
});
