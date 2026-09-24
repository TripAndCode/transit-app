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

  it("renders the reasons that excluded everything, when given", () => {
    render(<EmptyState title="Nothing here" reasons={["路線: A05", "運行種別: 平日"]} />);
    expect(screen.getByText("路線: A05")).toBeInTheDocument();
    expect(screen.getByText("運行種別: 平日")).toBeInTheDocument();
  });

  it("renders no reasons list when the array is empty", () => {
    const { container } = render(<EmptyState title="Nothing here" reasons={[]} />);
    expect(container.querySelector("ul")).not.toBeInTheDocument();
  });

  it("renders up to three recoveries as verb buttons and fires the tapped one", () => {
    const onClearRoutes = vi.fn();
    const onResetService = vi.fn();
    const onJumpLatest = vi.fn();
    render(
      <EmptyState
        title="Nothing here"
        recoveries={[
          { label: "路線指定を外す", onClick: onClearRoutes },
          { label: "運行種別をすべてに戻す", onClick: onResetService },
          { label: "最新のデータに移動する", onClick: onJumpLatest },
        ]}
      />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "運行種別をすべてに戻す" }));
    expect(onResetService).toHaveBeenCalledTimes(1);
    expect(onClearRoutes).not.toHaveBeenCalled();
    expect(onJumpLatest).not.toHaveBeenCalled();
  });

  it("renders only the first three recoveries when more are given", () => {
    const recoveries = [1, 2, 3, 4].map((n) => ({ label: `Recovery ${n}`, onClick: vi.fn() }));
    render(<EmptyState title="Nothing here" recoveries={recoveries} />);
    expect(screen.getAllByRole("button")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Recovery 4" })).not.toBeInTheDocument();
  });

  it("prefers recoveries over the legacy single action when both are given", () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="Nothing here"
        action={{ label: "Legacy", onClick }}
        recoveries={[{ label: "New", onClick: vi.fn() }]}
      />,
    );
    expect(screen.queryByRole("button", { name: "Legacy" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
  });
});
