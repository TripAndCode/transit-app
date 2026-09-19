import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverviewModal } from "./OverviewModal";

describe("OverviewModal", () => {
  it("renders nothing when closed", () => {
    render(
      <OverviewModal isOpen={false} onClose={() => {}} title="Detail">
        body
      </OverviewModal>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders an accessible dialog labelled by the title when open", () => {
    render(
      <OverviewModal isOpen onClose={() => {}} title="Route detail">
        body content
      </OverviewModal>,
    );
    const dialog = screen.getByRole("dialog", { name: "Route detail" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("closes on Escape via the shared Modal behavior", async () => {
    const onClose = vi.fn();
    render(
      <OverviewModal isOpen onClose={onClose} title="Route detail">
        body
      </OverviewModal>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes via the header close button", async () => {
    const onClose = vi.fn();
    render(
      <OverviewModal isOpen onClose={onClose} title="Route detail">
        body
      </OverviewModal>,
    );
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
