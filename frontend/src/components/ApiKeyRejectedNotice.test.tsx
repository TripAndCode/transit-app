import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { apiGet } from "../api/client";
import { ToastProvider } from "./ui/Toast";
import { ApiKeyRejectedNotice } from "./ApiKeyRejectedNotice";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("ApiKeyRejectedNotice", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("tells the user once that a rejected stored key was removed", async () => {
    await i18n.changeLanguage("en");
    localStorage.setItem("api_key", "stale");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(401, { detail: "Invalid API key" }))
        .mockResolvedValueOnce(json(200, {})),
    );
    render(
      <I18nextProvider i18n={i18n}>
        <ToastProvider>
          <ApiKeyRejectedNotice />
        </ToastProvider>
      </I18nextProvider>,
    );

    await act(() => apiGet("/api/config"));

    expect(screen.getByText(i18n.t("header.settings_api_key_rejected"))).toBeInTheDocument();
    expect(localStorage.getItem("api_key")).toBeNull();
  });
});
