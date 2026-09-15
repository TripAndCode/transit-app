import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LegendChip } from "./LegendChip";

describe("LegendChip", () => {
  it("renders the label and applies the given dot color", () => {
    render(<LegendChip color="#3E5C9A" label="最新報告" />);
    expect(screen.getByText("最新報告")).toBeInTheDocument();
    const dot = document.querySelector(".legend-chip__dot") as HTMLElement;
    expect(dot.style.background).toBe("rgb(62, 92, 154)");
  });
});
