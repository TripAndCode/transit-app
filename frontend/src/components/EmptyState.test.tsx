import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title, a default icon, and no action button by default", () => {
    const { container } = render(<EmptyState title="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders the hint when provided", () => {
    render(<EmptyState title="Nothing here" hint="Try a different filter" />);
    expect(screen.getByText("Try a different filter")).toBeInTheDocument();
  });

  it("renders a custom icon in place of the default when provided", () => {
    render(<EmptyState title="Nothing here" icon={<span data-testid="custom-icon" />} />);
    expect(screen.getByTestId("custom-icon")).toBeInTheDocument();
  });

  it("renders and fires the recovery action when provided", () => {
    const onClick = vi.fn();
    render(<EmptyState title="Nothing here" action={{ label: "Reset", onClick }} />);
    const button = screen.getByRole("button", { name: "Reset" });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
