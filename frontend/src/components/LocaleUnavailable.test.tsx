import i18next from "i18next";
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { initI18n } from "../i18n";
import { design } from "../i18n/design";
import { LocaleUnavailable } from "./LocaleUnavailable";

afterEach(() => localStorage.clear());

describe("LocaleUnavailable", () => {
  it("explains and offers a reload using only strings that ship in the entry", async () => {
    localStorage.setItem("app.locale", "ja");
    const instance = i18next.createInstance();
    const loaded = await initI18n(instance, {
      ja: () => Promise.reject(new Error("chunk removed by a deploy")),
      en: () => Promise.reject(new Error("chunk removed by a deploy")),
    });
    expect(loaded).toBe(false);

    render(
      <I18nextProvider i18n={instance}>
        <LocaleUnavailable />
      </I18nextProvider>,
    );
    expect(screen.getByRole("alert").textContent).toContain(design.ja.stringsUnavailable);
    expect(screen.getByRole("button", { name: design.ja.reload })).toBeTruthy();
  });
});
