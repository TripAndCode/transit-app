import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { mermaidMarkdownComponents } from "./MarkdownMermaid";
import "../i18n";

const FAKE_SVG = '<svg data-testid="fake-mermaid-svg"><rect /></svg>';

const renderMock = vi.fn().mockResolvedValue({ svg: FAKE_SVG });
const initializeMock = vi.fn();

vi.mock("mermaid", () => ({
  default: {
    initialize: (...args: unknown[]) => initializeMock(...args),
    render: (...args: unknown[]) => renderMock(...args),
  },
}));

const MERMAID_DOC = [
  "# Diagram",
  "",
  "```mermaid",
  "flowchart LR",
  "  a --> b",
  "```",
  "",
].join("\n");

const PLAIN_CODE_DOC = ["```js", "const x = 1;", "```"].join("\n");

beforeEach(() => {
  renderMock.mockClear();
  initializeMock.mockClear();
});

describe("mermaidMarkdownComponents", () => {
  it("renders a ```mermaid fence as a real <svg>, not just without throwing", async () => {
    const { container } = render(
      <ReactMarkdown components={mermaidMarkdownComponents}>{MERMAID_DOC}</ReactMarkdown>,
    );

    await waitFor(() => expect(renderMock).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector('[data-testid="fake-mermaid-svg"]')).not.toBeNull();
    // The literal ```mermaid source text must not also appear as a plain-text
    // code listing -- it's replaced by the diagram, not shown alongside it.
    expect(screen.queryByText(/flowchart LR/)).toBeNull();
  });

  it("passes the fence's own source text to mermaid.render", async () => {
    render(<ReactMarkdown components={mermaidMarkdownComponents}>{MERMAID_DOC}</ReactMarkdown>);
    await waitFor(() => expect(renderMock).toHaveBeenCalled());
    const [, source] = renderMock.mock.calls[0];
    expect(source).toContain("flowchart LR");
    expect(source).toContain("a --> b");
  });

  it("falls back to a <pre> of the raw source if mermaid.render rejects", async () => {
    renderMock.mockRejectedValueOnce(new Error("bad diagram"));
    render(<ReactMarkdown components={mermaidMarkdownComponents}>{MERMAID_DOC}</ReactMarkdown>);
    await waitFor(() => expect(screen.getByText(/flowchart LR/)).toBeTruthy());
  });

  it("leaves a non-mermaid fenced code block rendered normally", () => {
    render(<ReactMarkdown components={mermaidMarkdownComponents}>{PLAIN_CODE_DOC}</ReactMarkdown>);
    expect(screen.getByText("const x = 1;")).toBeTruthy();
    expect(renderMock).not.toHaveBeenCalled();
  });

  describe("aria-label", () => {
    it("uses the diagram's own title line when present", async () => {
      const doc = ["```mermaid", "flowchart LR", "  title Delay pipeline overview", "  a --> b", "```"].join("\n");
      render(<ReactMarkdown components={mermaidMarkdownComponents}>{doc}</ReactMarkdown>);
      await waitFor(() => expect(renderMock).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByRole("img", { name: "Delay pipeline overview" })).toBeTruthy());
    });

    it("falls back to the first node label when there is no title line", async () => {
      const doc = ["```mermaid", "flowchart LR", "  a[Ingest feed] --> b[Analyze]", "```"].join("\n");
      render(<ReactMarkdown components={mermaidMarkdownComponents}>{doc}</ReactMarkdown>);
      await waitFor(() => expect(renderMock).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByRole("img", { name: "Ingest feed" })).toBeTruthy());
    });

    it("falls back to a translated generic label when neither a title nor a node label can be found", async () => {
      render(<ReactMarkdown components={mermaidMarkdownComponents}>{MERMAID_DOC}</ReactMarkdown>);
      await waitFor(() => expect(renderMock).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByRole("img", { name: "Diagram" })).toBeTruthy());
    });
  });
});
