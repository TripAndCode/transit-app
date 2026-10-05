import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { decl, ruleBody } from "../test/cssRules";
import { HelpPage } from "./HelpPage";

// Two top-level (`## `) sections and no "Table of contents" section, so
// every section is listed and the first is current on opening.
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

  it("renders the manual's intro above the document", async () => {
    stubManualFetch(MANUAL_WITH_PREAMBLE);
    renderWithProviders(<HelpPage />);
    await screen.findByRole("heading", { name: "Section one", level: 2 });
    expect(screen.getByText("Intro paragraph before any section.")).toBeInTheDocument();
  });

  it("divides the sections from one another, but not the first from the page's title", () => {
    const css = readFileSync(resolve(__dirname, "../styles/global.css"), "utf8");
    expect(decl(ruleBody(css, ".user-manual-content h2:first-child"), "border-top")).toBe("none");
    expect(decl(ruleBody(css, ".user-manual-section + .user-manual-section > h2:first-child"), "border-top")).toBe(
      "1px solid var(--border-soft)",
    );
    expect(decl(ruleBody(css, ".user-manual-section,\n.user-manual-section [id] {"), "scroll-margin-top")).toBe("16px");
  });

  describe("one document", () => {
    const scrollIntoView = vi.fn();
    beforeEach(() => {
      scrollIntoView.mockReset();
      Element.prototype.scrollIntoView = scrollIntoView;
    });
    afterEach(() => {
      delete (Element.prototype as Partial<Element>).scrollIntoView;
    });

    /** Places each rendered section at `tops[i]` px from the top of the viewport. */
    function placeSections(tops: number[]) {
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
        const i = this.dataset.section == null ? -1 : [...document.querySelectorAll("[data-section]")].indexOf(this);
        const top = i === -1 ? 0 : tops[i];
        return { top, bottom: top + 400, height: 400, left: 0, right: 600, width: 600, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
      });
    }

    function scrollPage() {
      act(() => {
        document.dispatchEvent(new Event("scroll"));
      });
    }

    function current() {
      const nav = screen.getByRole("navigation", { name: "Manual sections" });
      return within(nav).queryByRole("button", { current: true })?.textContent ?? null;
    }

    it("renders every section, without the manual's own contents list", async () => {
      stubManualFetch(MANUAL_WITH_TOC);
      renderWithProviders(<HelpPage />);
      expect(await screen.findByRole("heading", { name: "Section one", level: 2 })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Section two", level: 2 })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Table of contents", level: 2 })).toBeNull();
      const nav = screen.getByRole("navigation", { name: "Manual sections" });
      expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(["Section one", "Section two"]);
      expect(screen.queryByRole("button", { name: /^Jump to:/ })).toBeNull();
    });

    it("shows a manual of one section, which no table of contents can be", async () => {
      stubManualFetch("# Delay Dashboard\n\n## Only section\n\nThe whole manual.\n");
      renderWithProviders(<HelpPage />);
      expect(await screen.findByRole("heading", { name: "Only section", level: 2 })).toBeInTheDocument();
      expect(screen.getByText("The whole manual.")).toBeInTheDocument();
    });

    it("opens at the page's title, with the first section current", async () => {
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });
      expect(current()).toBe("Section one");
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(window.location.hash).toBe("");
    });

    it("marks the section crossing the top third of the screen as the reader scrolls", async () => {
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section two", level: 2 });
      placeSections([-500, 100]);
      scrollPage();
      await waitFor(() => expect(current()).toBe("Section two"));
      placeSections([-50, 400]);
      scrollPage();
      await waitFor(() => expect(current()).toBe("Section one"));
    });

    it("marks the last section once the page can scroll no further", async () => {
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section two", level: 2 });
      placeSections([-900, 500]);
      const root = document.documentElement;
      const metrics = { scrollHeight: 2000, clientHeight: 800, scrollTop: 1200 };
      for (const [key, value] of Object.entries(metrics)) Object.defineProperty(root, key, { configurable: true, value });
      try {
        scrollPage();
        await waitFor(() => expect(current()).toBe("Section two"));
      } finally {
        for (const key of Object.keys(metrics)) delete (root as unknown as Record<string, unknown>)[key];
      }
    });

    it("takes a contents entry to its section, smoothly, and keeps it marked until the scroll ends", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section two", level: 2 });
      await user.click(screen.getByRole("button", { name: "Section two" }));
      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
      expect(scrollIntoView.mock.contexts[0]).toHaveAttribute("data-section");
      expect(scrollIntoView.mock.contexts[0]).toHaveTextContent("Other manual text.");
      expect(current()).toBe("Section two");
      expect(window.location.hash).toBe("#section-two");
      placeSections([-50, 400]);
      scrollPage();
      expect(current()).toBe("Section two");
      act(() => {
        document.dispatchEvent(new Event("scrollend"));
      });
      scrollPage();
      await waitFor(() => expect(current()).toBe("Section one"));
    });

    it("jumps instead of gliding when the reader asks for reduced motion", async () => {
      vi.spyOn(window, "matchMedia").mockImplementation(
        (query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList,
      );
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section two", level: 2 });
      await user.click(screen.getByRole("button", { name: "Section two" }));
      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
    });

    it("opens at a deep-linked heading", async () => {
      stubManualFetch(MANUAL_WITH_TOC);
      window.history.replaceState(null, "", "#section-two");
      renderWithProviders(<HelpPage />);
      const heading = await screen.findByRole("heading", { name: "Section two", level: 2 });
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" }));
      expect(scrollIntoView.mock.contexts[0]).toBe(heading);
    });
  });

  describe("search", () => {
    it("narrows the sidebar and content to sections matching the query text", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });

      await user.type(screen.getByRole("searchbox"), "Other manual");

      expect(screen.queryByRole("button", { name: "Section one" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Section two" })).toBeInTheDocument();
      expect(await screen.findByRole("heading", { name: "Section two", level: 2 })).toBeInTheDocument();
      expect(screen.getByText("Other manual text.")).toBeInTheDocument();
    });

    it("matches on a section's own title, not just its body", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });

      await user.type(screen.getByRole("searchbox"), "section two");

      expect(screen.queryByRole("button", { name: "Section one" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Section two" })).toBeInTheDocument();
    });

    it("announces the match count in a live status region", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });

      await user.type(screen.getByRole("searchbox"), "Other manual");

      expect(await screen.findByRole("status")).toHaveTextContent("1");
    });

    it("shows a no-matches message instead of stale content when nothing matches", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });

      await user.type(screen.getByRole("searchbox"), "nonexistent phrase xyz");

      expect(screen.getByText("No matches")).toBeInTheDocument();
      expect(screen.queryByText("Some manual text.")).not.toBeInTheDocument();
      expect(screen.queryByText("Other manual text.")).not.toBeInTheDocument();
    });

    it("shows the whole manual again once the query is cleared", async () => {
      const user = userEvent.setup();
      renderWithProviders(<HelpPage />);
      await screen.findByRole("heading", { name: "Section one", level: 2 });

      const search = screen.getByRole("searchbox");
      await user.type(search, "Some manual");
      expect(screen.queryByRole("heading", { name: "Section two", level: 2 })).toBeNull();
      await user.clear(search);

      expect(screen.getByRole("heading", { name: "Section one", level: 2 })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Section two", level: 2 })).toBeInTheDocument();
    });
  });
});
