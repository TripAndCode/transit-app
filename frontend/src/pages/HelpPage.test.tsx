import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { HelpPage } from "./HelpPage";

describe("HelpPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: () => Promise.resolve("## Section one\n\nSome manual text.\n\n![alt](./01-x.png)"),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the English manual for the active locale and renders it as markdown", async () => {
    renderWithProviders(<HelpPage />);
    expect(fetch).toHaveBeenCalledWith("/user-manual/en.md");
    expect(await screen.findByRole("heading", { name: "Section one", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Some manual text.")).toBeInTheDocument();
  });

  it("rewrites relative image paths to the manual asset directory", async () => {
    renderWithProviders(<HelpPage />);
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("src", "/user-manual/01-x.png");
  });

  it("shows an error banner when the manual can't be fetched", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    renderWithProviders(<HelpPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
