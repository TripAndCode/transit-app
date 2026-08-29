import { useEffect, useId, useState } from "react";
import type { Components } from "react-markdown";

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
 *  Callers key this component by `source` (see `code()` below) so a
 *  *different* diagram forces a full remount instead of this component
 *  resetting `svg`/`failed` itself -- React clears state on remount for
 *  free, without a synchronous setState call in the effect body
 *  (`react-hooks/set-state-in-effect`, an error in this repo's ESLint
 *  config, only allows setState from a callback reacting to the external
 *  `mermaid.render()` promise settling, not from the effect body itself). */
function MermaidDiagram({ source }: { source: string }) {
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
  return <div role="img" aria-label="diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function isMermaidClassName(className: string | undefined): boolean {
  return typeof className === "string" && /\blanguage-mermaid\b/.test(className);
}

function codeToText(children: React.ReactNode): string {
  return (Array.isArray(children) ? children.join("") : String(children ?? "")).replace(/\n$/, "");
}

/** `react-markdown` `components` override: renders a ```mermaid fenced code
 *  block as a live SVG diagram instead of a literal code listing; every
 *  other fence/inline-code span renders exactly as react-markdown's own
 *  default. Kept as one shared object (not redefined per caller) so
 *  `HelpPage`-style consumers can reuse it verbatim for any future feature
 *  doc that embeds a mermaid diagram, not just the architecture page. */
export const mermaidMarkdownComponents: Components = {
  pre({ children }) {
    const child = Array.isArray(children) ? children[0] : children;
    if (
      child &&
      typeof child === "object" &&
      "props" in child &&
      isMermaidClassName((child.props as { className?: string }).className)
    ) {
      // Render the diagram directly, unwrapped -- letting the default <pre>
      // through here would nest a <div> (the diagram, via the `code`
      // override below) inside a <pre>, which is invalid HTML.
      return <>{child}</>;
    }
    return <pre>{children}</pre>;
  },
  code({ className, children, ...rest }) {
    if (isMermaidClassName(className)) {
      const source = codeToText(children);
      // Keyed by source (see MermaidDiagram's own doc comment): switching to
      // a different diagram remounts rather than reusing state across an
      // unrelated `source`.
      return <MermaidDiagram key={source} source={source} />;
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  },
};
