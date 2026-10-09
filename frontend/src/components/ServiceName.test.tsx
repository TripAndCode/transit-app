import { afterEach, describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import i18n from "../i18n";
import { ServiceName } from "./ServiceName";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("ServiceName", () => {
  it("shows a known service in the UI's language", async () => {
    await i18n.changeLanguage("en");
    const { container } = render(<ServiceName value="平日" />); // i18n-ignore: GTFS service name
    expect(container).toHaveTextContent("Weekday");
    expect(container.querySelector("[lang]")).toBeNull();
  });

  it("marks an operator's untranslated Japanese name as Japanese in an English screen", async () => {
    await i18n.changeLanguage("en");
    const { container } = render(<ServiceName value="お盆臨時　20日" />); // i18n-ignore: GTFS service name
    expect(container.querySelector('[lang="ja"]')).toHaveTextContent("お盆臨時 20日");
  });

  it("needs no marking on a Japanese screen, or for a name that is not Japanese", async () => {
    await i18n.changeLanguage("ja");
    expect(render(<ServiceName value="お盆臨時　20日" />).container.querySelector("[lang]")).toBeNull(); // i18n-ignore: GTFS service name
    await i18n.changeLanguage("en");
    expect(render(<ServiceName value="Holiday Special" />).container.querySelector("[lang]")).toBeNull();
  });
});
