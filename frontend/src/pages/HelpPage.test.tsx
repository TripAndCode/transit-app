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
        text: () =>
          Promise.resolve(
            "# Delay Dashboard\n\n## Section one\n\nSome manual text.\n\n![alt](./01-x.png)\n\n" +
              "| A | B |\n|---|---|\n| 1 | 2 |\n",
          ),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the English manual for the active locale and renders it as markdown", async () => {
    renderWithProviders(<HelpPage />);
    expect(await screen.findByRole("heading", { name: "Section one", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Some manual text.")).toBeInTheDocument();
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe("/user-manual/en.md");
  });

  it("strips the manual's own top-level title (redundant with the page's own <h1>)", async () => {
    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });
    expect(screen.queryByRole("heading", { name: "Delay Dashboard", level: 1 })).not.toBeInTheDocument();
  });

  it("renders GFM pipe tables as real tables, not literal text", async () => {
    renderWithProviders(<HelpPage />);
    const table = await screen.findByRole("table");
    expect(table).toHaveTextContent("A");
    expect(table).toHaveTextContent("1");
  });

  it("rewrites relative image paths to the manual asset directory", async () => {
    renderWithProviders(<HelpPage />);
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("src", "/user-manual/01-x.png");
  });

  it("shows a retry-capable error banner when the manual can't be fetched", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, text: () => Promise.resolve("") }),
    );
    renderWithProviders(<HelpPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });
});
