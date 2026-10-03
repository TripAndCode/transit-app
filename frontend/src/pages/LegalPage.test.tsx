import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { LegalPage } from "./LegalPage";

function renderDoc(doc: "privacy" | "terms") {
  return renderWithProviders(
    <MemoryRouter>
      <LegalPage doc={doc} />
    </MemoryRouter>,
  );
}

function stubDocFetch(markdown: string, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok, status: ok ? 200 : 404, text: () => Promise.resolve(markdown) }),
  );
}

describe("LegalPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetches the privacy policy for the active locale and renders it under the page's own heading", async () => {
    stubDocFetch("# Privacy Policy\n\nLast updated: today\n\n## Operator\n\nTripAndCode\n");
    renderDoc("privacy");

    expect(await screen.findByRole("heading", { name: "Operator", level: 2 })).toBeInTheDocument();
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe("/legal/privacy.en.md");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Privacy Policy", level: 1 })).toBeInTheDocument();
  });

  it("fetches the terms document on the terms page", async () => {
    stubDocFetch("# Terms of Service\n\n## No warranty\n\nAs is.\n");
    renderDoc("terms");

    expect(await screen.findByRole("heading", { name: "No warranty", level: 2 })).toBeInTheDocument();
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe("/legal/terms.en.md");
    expect(screen.getByRole("heading", { name: "Terms of Service", level: 1 })).toBeInTheDocument();
  });

  it("shows a retry-capable error banner when the document can't be fetched", async () => {
    stubDocFetch("", false);
    renderDoc("privacy");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("says the document is loading until it arrives", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    renderDoc("terms");

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("links back to the app", () => {
    stubDocFetch("# Privacy Policy\n");
    renderDoc("privacy");

    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/");
  });
});
