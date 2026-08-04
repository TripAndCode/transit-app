import { describe, it, expect, afterAll } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RangeBadge } from "./RangeBadge";
import i18n from "../i18n";

function renderBadge(query: string) {
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/?${query}`]}>
      <RangeBadge />
    </MemoryRouter>,
  );
}

describe("RangeBadge custom-range label locale", () => {
  afterAll(async () => await i18n.changeLanguage("en"));

  it("shows the English label with ISO-style dashes for a custom range, not the ja-only slash form", async () => {
    await i18n.changeLanguage("en");
    renderBadge("from=2026-06-25&to=2026-07-24");
    expect(screen.getByText(/2026-06-25/)).toBeInTheDocument();
    expect(screen.queryByText(/2026\/06\/25/)).toBeNull();
  });

  it("shows the Japanese-conventional slash form when the active language is ja", async () => {
    await i18n.changeLanguage("ja");
    renderBadge("from=2026-06-25&to=2026-07-24");
    expect(screen.getByText(/2026\/06\/25/)).toBeInTheDocument();
  });

  it("sets the date input's lang attribute to the active UI language", async () => {
    await i18n.changeLanguage("en");
    renderBadge("from=2026-06-25&to=2026-07-24");
    fireEvent.click(screen.getByRole("button", { name: /2026/ }));
    const inputs = document.querySelectorAll("input[type='date']");
    expect(inputs.length).toBeGreaterThan(0);
    inputs.forEach((el) => expect(el.getAttribute("lang")).toBe("en"));
  });
});
