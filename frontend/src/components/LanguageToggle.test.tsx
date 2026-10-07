import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n, * as i18nModule from "../i18n";
import { LanguageToggle } from "./LanguageToggle";

function renderToggle() {
  return render(
    <I18nextProvider i18n={i18n}>
      <LanguageToggle />
    </I18nextProvider>,
  );
}

describe("LanguageToggle", () => {
  afterEach(async () => {
    vi.restoreAllMocks();
    await i18n.changeLanguage("en");
  });

  it("names each language in itself and marks the current one", () => {
    renderToggle();
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "日本語" })).toHaveAttribute("aria-pressed", "false");
  });

  it("says so when the other language could not be loaded, rather than ignoring the click", async () => {
    vi.spyOn(i18nModule, "changeLocale").mockResolvedValue(false);
    renderToggle();
    await userEvent.click(screen.getByRole("button", { name: "日本語" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Couldn't switch the language");
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
  });
});
