import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { HelpPage } from "./HelpPage";

// Two top-level (`## `) sections -- enough to exercise the sidebar without a
// "Table of contents" section, so the default (no location.hash) case lands
// straight on the first real section instead of a ToC page.
const TWO_SECTION_MANUAL =
  "# Delay Dashboard\n\n" +
  "## Section one\n\nSome manual text.\n\n![alt](./01-x.png)\n\n" +
  "| A | B |\n|---|---|\n| 1 | 2 |\n\n" +
  "## Section two\n\nOther manual text.\n";

// Mirrors the real manual's shape: a "Table of contents" section whose links
// are the deep-link anchors the initial-hash match relies on.
const MANUAL_WITH_TOC =
  "# Delay Dashboard\n\n" +
  "## Table of contents\n\n" +
  "1. [Section one](#section-one)\n" +
  "2. [Section two](#section-two)\n\n" +
  "## Section one\n\nSome manual text.\n\n" +
  "## Section two\n\nOther manual text.\n";

// Mirrors the real manuals' shape further: intro prose before the first `## `
// heading, kept separate from any section so it stays visible regardless of
// which section is selected (see HelpPage.tsx's `preamble`).
const MANUAL_WITH_PREAMBLE =
  "# Delay Dashboard\n\n" +
  "Intro paragraph before any section.\n\n" +
  "## Table of contents\n\n" +
  "1. [Section one](#section-one)\n" +
  "2. [Section two](#section-two)\n\n" +
  "## Section one\n\nSome manual text.\n\n" +
  "## Section two\n\nOther manual text.\n";

function stubManualFetch(markdown: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve(markdown) }),
  );
}

describe("HelpPage", () => {
  beforeEach(() => {
    stubManualFetch(TWO_SECTION_MANUAL);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    // The component syncs location.hash to the active section (replaceState,
    // so it doesn't fire hashchange/reload) -- reset it so one test's
    // selection can't leak into the next test's initial-hash match.
    window.history.replaceState(null, "", window.location.pathname);
  });

  it("fetches the English manual for the active locale and renders its first section", async () => {
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

  it("lists every top-level section in the sidebar with the first section active initially", async () => {
    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });

    expect(screen.getByRole("button", { name: "Section one" })).toHaveAttribute("aria-current", "true");
    const sectionTwoButton = screen.getByRole("button", { name: "Section two" });
    expect(sectionTwoButton).not.toHaveAttribute("aria-current");
  });

  it("clicking a different sidebar entry renders only that section's content", async () => {
    const user = userEvent.setup();
    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });

    await user.click(screen.getByRole("button", { name: "Section two" }));

    expect(await screen.findByRole("heading", { name: "Section two", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Other manual text.")).toBeInTheDocument();
    // Section one's own content -- not just its (still-visible) sidebar
    // entry -- must be gone: only the selected section renders.
    expect(screen.queryByRole("heading", { name: "Section one", level: 2 })).not.toBeInTheDocument();
    expect(screen.queryByText("Some manual text.")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("updates the active-section sidebar highlight when a different entry is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });

    await user.click(screen.getByRole("button", { name: "Section two" }));
    await screen.findByRole("heading", { name: "Section two", level: 2 });

    expect(screen.getByRole("button", { name: "Section two" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "Section one" })).not.toHaveAttribute("aria-current");
  });

  it("selects the section matching an existing #anchor deep link on load", async () => {
    stubManualFetch(MANUAL_WITH_TOC);
    window.history.replaceState(null, "", "#section-two");

    renderWithProviders(<HelpPage />);

    expect(await screen.findByRole("heading", { name: "Section two", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Section two" })).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("heading", { name: "Table of contents", level: 2 })).not.toBeInTheDocument();
  });

  it("skips the Table of contents section for the default (no-hash) view, landing on the first real section", async () => {
    stubManualFetch(MANUAL_WITH_TOC);

    renderWithProviders(<HelpPage />);

    expect(await screen.findByRole("heading", { name: "Section one", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Section one" })).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("heading", { name: "Table of contents", level: 2 })).not.toBeInTheDocument();
  });

  it("renders the manual's intro preamble regardless of which section is selected", async () => {
    const user = userEvent.setup();
    stubManualFetch(MANUAL_WITH_PREAMBLE);

    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });
    expect(screen.getByText("Intro paragraph before any section.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Section two" }));
    await screen.findByRole("heading", { name: "Section two", level: 2 });
    expect(screen.getByText("Intro paragraph before any section.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Table of contents" }));
    await screen.findByRole("heading", { name: "Table of contents", level: 2 });
    expect(screen.getByText("Intro paragraph before any section.")).toBeInTheDocument();
  });
});
