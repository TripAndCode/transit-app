import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { SettingsDrawer } from "./SettingsDrawer";

function renderDrawer() {
  const onClose = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <SettingsDrawer open onClose={onClose} />
    </I18nextProvider>,
  );
  return { onClose };
}

describe("SettingsDrawer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("opens with an empty key field where web storage is blocked", () => {
    const blocked = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked, removeItem: blocked, clear: () => {} });
    renderDrawer();
    expect(screen.getByPlaceholderText(i18n.t("header.settings_api_key_placeholder"))).toHaveValue("");
  });

  it("saves the key without the whitespace pasted around it", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDrawer();
    await user.type(screen.getByPlaceholderText(i18n.t("header.settings_api_key_placeholder")), "  pro-key ");
    await user.click(screen.getByRole("button", { name: i18n.t("common.save") }));
    expect(localStorage.getItem("api_key")).toBe("pro-key");
    expect(onClose).toHaveBeenCalled();
  });
});
