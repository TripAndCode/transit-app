import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { TabExplorer } from "./TabExplorer";

const FAKE_MANUAL = `# Delay Dashboard — User Manual

## 3. Overview tab — "what's happening right now"

Overview excerpt paragraph.

## 4. Map tab — "where it's happening"

Map excerpt paragraph.

## 5. Analysis tab — "when and why delays happen"

Analysis excerpt paragraph.

## 6. Agencies tab — "how you compare to others"

Agencies excerpt paragraph.

## 7. Latest observations tab — "the buses right now"

Live excerpt paragraph.

## 8. Ask tab — ask in a conversation

Ask excerpt paragraph.
`;

describe("TabExplorer", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, text: async () => FAKE_MANUAL })),
    );
  });

  it("shows the Overview tab's preview and manual excerpt by default", async () => {
    renderWithProviders(<TabExplorer />);
    expect(screen.getByText("What's happening right now")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Overview excerpt paragraph.")).toBeTruthy());
  });

  it("swaps the panel to the matching preview and excerpt when another tab is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TabExplorer />);
    await waitFor(() => expect(screen.getByText("Overview excerpt paragraph.")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: /Agencies/ }));

    expect(screen.getByText("How you compare to others")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Agencies excerpt paragraph.")).toBeTruthy());
    expect(screen.queryByText("Overview excerpt paragraph.")).toBeNull();
  });

  it("has exactly one navigation pattern: only the list buttons, no second widget", () => {
    renderWithProviders(<TabExplorer />);
    // Six tabs, one button each -- nothing else interactive on the page.
    expect(screen.getAllByRole("button")).toHaveLength(6);
  });
});
