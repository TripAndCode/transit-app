import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatTile } from "./StatTile";

describe("StatTile", () => {
  it("renders the label and value", () => {
    render(<StatTile label="観測便" value="3" />);
    expect(screen.getByText("観測便")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("applies the flagged color class only when flagged is true", () => {
    const { container, rerender } = render(<StatTile label="遅延" value="2" />);
    expect(container.querySelector(".stat-tile__value--flagged")).toBeNull();
    rerender(<StatTile label="遅延" value="2" flagged />);
    expect(container.querySelector(".stat-tile__value--flagged")).not.toBeNull();
  });

  it("formats a numeric value via useCountUp and appends the suffix", () => {
    render(<StatTile label="On-time" value={87} suffix="%" />);
    expect(screen.getByText("87%")).toBeInTheDocument();
  });

  it("keeps a pre-formatted string value unanimated and as-is", () => {
    render(<StatTile label="観測便" value="3" />);
    // A string value is rendered verbatim -- no useCountUp/toLocaleString pass.
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
