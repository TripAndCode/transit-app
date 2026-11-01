import { afterEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { CsvLink } from "./CsvLink";

const HREF = "/api/1/reports/ranking?from=2026-06-01&to=2026-06-30&format=csv";

describe("CsvLink", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  // A plain link sends the browser's Accept-Language, which need not be the
  // UI's: the jsdom browser here is en-US, so the Japanese case is the one a
  // missing parameter would get wrong.
  it.each(["ja", "en"] as const)("asks for the file's credit in the UI language (%s)", async (locale) => {
    await i18n.changeLanguage(locale);
    renderWithProviders(<CsvLink href={HREF} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", `${HREF}&lang=${locale}`);
  });

  it("reads a regional language as its UI language", async () => {
    await i18n.changeLanguage("en-GB");
    renderWithProviders(<CsvLink href={HREF} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", `${HREF}&lang=en`);
  });
});
