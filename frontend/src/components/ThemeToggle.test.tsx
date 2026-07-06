import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { ThemeToggle } from "./ThemeToggle";

function wrap() {
  return render(
    <I18nextProvider i18n={i18n}>
      <ThemeToggle />
    </I18nextProvider>
  );
}

describe("ThemeToggle", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("renders a button labeled for switching to light mode when dark is active", () => {
    wrap();
    expect(screen.getByRole("button", { name: /switch to light mode/i })).toBeTruthy();
  });

  it("toggles data-theme and its own label on click", async () => {
    const user = userEvent.setup();
    wrap();
    const btn = screen.getByRole("button", { name: /switch to light mode/i });
    await user.click(btn);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: /switch to dark mode/i })).toBeTruthy();
  });
});
