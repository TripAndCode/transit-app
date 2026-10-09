import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { LiveMapHero } from "./LiveMapHero";

describe("LiveMapHero", () => {
  it("renders a decorative canvas, even without 2D canvas support", () => {
    // jsdom returns null from getContext("2d"), which the hook must tolerate.
    const { container, unmount } = render(
      <I18nextProvider i18n={i18n}>
        <LiveMapHero />
      </I18nextProvider>,
    );
    const canvas = container.querySelector("canvas");
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    expect(() => unmount()).not.toThrow();
  });
});
