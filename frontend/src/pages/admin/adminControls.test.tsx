import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./adminControls";

describe("AdminButton", () => {
  it("applies the variant class alongside the shared admin-btn class", () => {
    render(<AdminButton variant="danger">Delete</AdminButton>);
    const btn = screen.getByRole("button", { name: "Delete" });
    expect(btn.className).toContain("admin-btn");
    expect(btn.className).toContain("danger");
  });

  it("forwards native button props like disabled and onClick", () => {
    render(<AdminButton variant="primary" disabled>Save</AdminButton>);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("StatusChip", () => {
  it("uses accent colors for the good tone", () => {
    render(<StatusChip tone="good">Active</StatusChip>);
    const chip = screen.getByText("Active");
    expect(chip.style.color).toBe("var(--accent)");
    expect(chip.style.background).toBe("var(--accent-soft)");
  });

  it("uses warning colors for the warn tone", () => {
    render(<StatusChip tone="warn">Suspended</StatusChip>);
    const chip = screen.getByText("Suspended");
    expect(chip.style.color).toBe("var(--color-warning, #C99A2E)");
    expect(chip.style.background).toBe("var(--surface-2)");
  });

  it("uses muted colors for the neutral tone", () => {
    render(<StatusChip tone="neutral">Deleted</StatusChip>);
    const chip = screen.getByText("Deleted");
    expect(chip.style.color).toBe("var(--text-tertiary)");
    expect(chip.style.background).toBe("var(--surface-2)");
  });
});

describe("AdminAvatar", () => {
  it("renders the uppercased first letter of the label", () => {
    render(<AdminAvatar label="tanaka mai" />);
    expect(screen.getByText("T")).toBeTruthy();
  });
});

describe("AdminSearchInput", () => {
  it("renders a search input with the given placeholder", () => {
    render(<AdminSearchInput placeholder="Search by email / name" />);
    expect(screen.getByPlaceholderText("Search by email / name")).toBeTruthy();
  });
});
