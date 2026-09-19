import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("test pipeline", () => {
  it("renders jsx in jsdom", () => {
    render(<p>umax-frontend-ready</p>);
    expect(screen.getByText("umax-frontend-ready")).toBeInTheDocument();
  });
});
