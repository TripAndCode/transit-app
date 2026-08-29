import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";
import { AdminArchitecturePage } from "../AdminArchitecturePage";

// The Mermaid renderer itself (does it produce a real <svg>?) is covered by
// MarkdownMermaid.test.tsx -- this page test only cares about the doc-list
// wiring (fetch, select, render), so `mermaidMarkdownComponents` is mocked
// down to plain passthrough rendering, keeping this suite independent of
// the `mermaid` package/its async render lifecycle entirely.
vi.mock("../../../components/MarkdownMermaid", () => ({
  mermaidMarkdownComponents: {},
}));

let docsReturnValue: any;
let docReturnValue: any;

vi.mock("../../../api/admin", () => ({
  useArchitectureDocs: () => docsReturnValue,
  useArchitectureDoc: (slug: string | null) => (slug == null ? { data: undefined, error: null } : docReturnValue),
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

const DOCS = [
  { slug: "ask-tab", title: "Ask tab" },
  { slug: "map-tab", title: "Map tab" },
];

beforeEach(() => {
  docsReturnValue = { data: DOCS, error: null };
  docReturnValue = {
    data: { slug: "ask-tab", title: "Ask tab", content: "# Ask tab\n\nDetails about the Ask tab." },
    error: null,
  };
});

describe("AdminArchitecturePage", () => {
  it("renders the diagram section title", () => {
    wrap(<AdminArchitecturePage />);
    expect(screen.getByText(/data flow/i)).toBeTruthy();
  });

  it("lists every feature doc in the sidebar", () => {
    wrap(<AdminArchitecturePage />);
    expect(screen.getByRole("button", { name: "Ask tab" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Map tab" })).toBeTruthy();
  });

  it("defaults to the first doc's content", () => {
    wrap(<AdminArchitecturePage />);
    expect(screen.getByText(/Details about the Ask tab/)).toBeTruthy();
  });

  it("switches content when a different sidebar entry is clicked", () => {
    docReturnValue = {
      data: { slug: "map-tab", title: "Map tab", content: "Details about the Map tab." },
      error: null,
    };
    wrap(<AdminArchitecturePage />);
    fireEvent.click(screen.getByRole("button", { name: "Map tab" }));
    expect(screen.getByText(/Details about the Map tab/)).toBeTruthy();
  });

  it("shows an empty-state message when no feature docs exist", () => {
    docsReturnValue = { data: [], error: null };
    wrap(<AdminArchitecturePage />);
    expect(screen.getByText(/no feature docs found/i)).toBeTruthy();
  });

  it("shows an error banner when the doc list fails to load", () => {
    docsReturnValue = { data: undefined, error: new Error("network down") };
    wrap(<AdminArchitecturePage />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
