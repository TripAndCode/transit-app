import type { Components } from "react-markdown";
import { MermaidDiagram } from "./MermaidDiagram";

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
