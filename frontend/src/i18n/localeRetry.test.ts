import i18next from "i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initI18n } from ".";

// A browser keeps a failed module import failed for the rest of the page, so
// retrying the same import() never reaches the network again. The default
// loaders therefore retry through fetch; this file makes the chunk import
// fail to exercise that path.
vi.mock("./locales/ja.json", () => {
  throw new Error("chunk fetch failed");
});

afterEach(() => localStorage.clear());

describe("default translation loaders", () => {
  it("retry a failed chunk import by fetching the language's JSON", async () => {
    localStorage.setItem("app.locale", "ja");
    const fetchJson = vi.fn(async () => new Response(JSON.stringify({ common: { language_aria: "言語" } })));
    vi.stubGlobal("fetch", fetchJson);

    const instance = i18next.createInstance();
    const loaded = await initI18n(instance);

    expect(fetchJson).toHaveBeenCalledTimes(1);
    expect(String((fetchJson.mock.calls[0] as unknown[])[0])).toMatch(/locales\/ja\.json$/);
    expect(loaded).toBe(true);
    expect(instance.t("common.language_aria")).toBe("言語");
  });
});
