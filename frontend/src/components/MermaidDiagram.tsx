import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

/** Lazily imported so `mermaid` (a sizeable rendering library, similar in
 *  spirit to why MapLibre is kept out of the entry chunk) only loads on the
 *  admin-only pages that actually render a ```mermaid fence, never as part
 *  of the main app bundle. */
let mermaidInitPromise: Promise<typeof import("mermaid")> | null = null;

async function loadMermaid() {
  if (!mermaidInitPromise) {
    mermaidInitPromise = import("mermaid")
      .then((m) => {
        const mermaid = m.default;
        // `strict` sanitizes the rendered SVG's own markup (labels etc.) --
        // relevant even though today's only callers are our own docs/CLAUDE.md
        // content, not arbitrary user input.
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        return m;
      })
      .catch((err: unknown) => {
        // Reset so a transient failure (network blip, stale chunk hash)
        // doesn't permanently disable mermaid rendering for the rest of
        // the SPA session -- the next caller gets a fresh import attempt.
        mermaidInitPromise = null;
        throw err;
      });
  }
  return mermaidInitPromise;
}

/** Renders one ```mermaid fenced block's source as an SVG diagram via
 *  `mermaid.render()`. Falls back to the raw source text (in a `<pre>`) if
 *  mermaid fails to parse it -- a malformed diagram degrades to visible
 *  text, never a blank pane or a thrown render error.
 *
 *  Callers key this component by `source` (see MarkdownMermaid's `code()`
 *  override) so a *different* diagram forces a full remount instead of this
 *  component resetting `svg`/`failed` itself -- React clears state on
 *  remount for free, without a synchronous setState call in the effect body
 *  (`react-hooks/set-state-in-effect`, an error in this repo's ESLint
 *  config, only allows setState from a callback reacting to the external
 *  `mermaid.render()` promise settling, not from the effect body itself). */
const TITLE_LINE = /^\s*title\s*:?\s+(.+?)\s*$/im;
const NODE_LABEL = /[[({]([^[\](){}]{1,80})[\])}]/;

/** Best-effort human label for a diagram's `aria-label`: the source's own
 *  `title` directive (supported by most mermaid diagram types) when
 *  present, else the first node's bracketed/parenthesized label, else
 *  `fallback`. Never throws on unparseable source -- worst case is the
 *  fallback, matching the raw-`<pre>` degrade this component already uses
 *  for a `mermaid.render()` failure. */
function deriveDiagramLabel(source: string, fallback: string): string {
  const titleMatch = source.match(TITLE_LINE);
  if (titleMatch?.[1]) return titleMatch[1].trim();

  const nodeMatch = source.match(NODE_LABEL);
  const nodeLabel = nodeMatch?.[1]?.trim().replace(/^["']|["']$/g, "");
  if (nodeLabel) return nodeLabel;

  return fallback;
}

export function MermaidDiagram({ source }: { source: string }) {
  const { t } = useTranslation();
  const reactId = useId();
  const diagramId = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then((m) => m.default.render(diagramId, source))
      .then(({ svg: rendered }) => {
        if (!cancelled) setSvg(rendered);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [diagramId, source]);

  if (failed) return <pre>{source}</pre>;
  if (svg == null) return null;
  // mermaid's own `securityLevel: "strict"` sanitizes the markup it hands
  // back, so this mirrors the same trust boundary `dangerouslySetInnerHTML`
  // already has elsewhere in the app for other library-rendered SVG. (No
  // `react/no-danger` rule is configured in this repo's eslint.config.js to
  // suppress -- jsx-a11y doesn't ship an equivalent -- so no disable comment
  // is needed here.)
  const label = deriveDiagramLabel(source, t("markdownMermaid.diagram_fallback_label"));
  return <div role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: svg }} />;
}
