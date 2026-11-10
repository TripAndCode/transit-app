import { afterEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { LegalLinks } from "./LegalLinks";

const REPO = "https://github.com/TripAndCode/transit-app/blob/main/docs/";

describe("LegalLinks", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("links the terms, the privacy policy and the English methodology", async () => {
    await i18n.changeLanguage("en");
    renderWithProviders(
      <MemoryRouter>
        <LegalLinks />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "Terms and policies" });
    expect(nav).toContainElement(screen.getByRole("link", { name: "Terms of Service" }));
    expect(screen.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/privacy");
    const methodology = screen.getByRole("link", { name: "Methodology" });
    expect(methodology).toHaveAttribute("href", `${REPO}methodology.en.md`);
    expect(methodology).toHaveAttribute("target", "_blank");
  });

  it("links the Japanese methodology to a Japanese reader", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(
      <MemoryRouter>
        <LegalLinks />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: i18n.t("legal.methodology") })).toHaveAttribute(
      "href",
      `${REPO}methodology.md`,
    );
  });
});
