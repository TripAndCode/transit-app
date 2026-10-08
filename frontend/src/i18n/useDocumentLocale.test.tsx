import { afterEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import i18n from ".";
import { useDocumentLocale } from "./useDocumentLocale";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("useDocumentLocale", () => {
  it("names the page and its language, and follows a language switch", async () => {
    await i18n.changeLanguage("en");
    renderHook(() => useDocumentLocale());
    expect(document.title).toBe("Delay Dashboard");
    expect(document.documentElement.lang).toBe("en");
    await act(async () => {
      await i18n.changeLanguage("ja");
    });
    expect(document.title).toBe("遅延ダッシュボード");
    expect(document.documentElement.lang).toBe("ja");
  });

  it("leads the title with the page and its agency, separated as the app separates them", async () => {
    await i18n.changeLanguage("en");
    renderHook(() => useDocumentLocale(["Routes", "Aomori City Bus"]));
    expect(document.title).toBe("Routes · Aomori City Bus · Delay Dashboard");
  });

  it("leaves out parts it doesn't have", async () => {
    await i18n.changeLanguage("en");
    renderHook(() => useDocumentLocale(["Account", null]));
    expect(document.title).toBe("Account · Delay Dashboard");
  });
});
