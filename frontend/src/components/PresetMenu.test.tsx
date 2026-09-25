import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { PresetMenu } from "./PresetMenu";
import * as auth from "../api/auth";

describe("PresetMenu (anonymous)", () => {
  it("replaces the native title on the login hint with a keyboard-reachable Tooltip", () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: null } as never);
    renderWithProviders(
      <PresetMenu
        agencyId={1}
        currentRangeCtx={{ from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all", service: "all", routes: [] }}
        onSelect={() => {}}
      />,
    );
    const hint = screen.getByText("Presets");
    expect(hint).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.focusIn(hint);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Sign in to save");
    fireEvent.focusOut(hint);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
