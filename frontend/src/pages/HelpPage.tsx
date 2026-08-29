import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import { ApiError } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

const MANUAL_BASE = "/user-manual";

/** The manual's own top-level `# Title` line is redundant with this page's
 *  own <h1> (and differently styled, since react-markdown's h1 has no CSS of
 *  its own) -- strip it before rendering. Kept in the source .md so the file
 *  still reads correctly viewed directly on GitHub. */
function stripLeadingH1(markdown: string): string {
  return markdown.replace(/^#\s.*\n+/, "");
}

async function fetchManual(locale: string, signal: AbortSignal): Promise<string> {
  const r = await fetch(`${MANUAL_BASE}/${locale}.md`, { signal });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new ApiError(r.status, body);
  }
  return r.text();
}

type ManualSection = {
  /** Raw heading text, e.g. `5. Analysis tab — "when and why delays happen"
   *  [most important]` -- used verbatim as the sidebar label. */
  title: string;
  /** This section's own markdown, from its `## ` line to (exclusive of) the
   *  next top-level heading. Always starts with that `## ` line -- the
   *  manual's intro prose (if any) is returned separately as `preamble`,
   *  not folded into this section, so every section's rendered `<h2>` is a
   *  true first child (see global.css's `.user-manual-content h2:first-child`
   *  reset) and the intro isn't hidden behind whichever section happens to
   *  be selected. */
  markdown: string;
};

type SplitResult = {
  /** Any prose above the manual's first `## ` heading (e.g. the intro
   *  paragraphs). Rendered unconditionally, above the section sidebar, so
   *  it's visible regardless of which section is selected -- nothing from
   *  the source is silently dropped or hidden behind a non-default section. */
  preamble: string;
  sections: ManualSection[];
};

const TOP_HEADING_RE = /^##\s+(.+?)\s*$/;

/** Splits the manual body (already stripped of its leading H1) into its
 *  leading preamble prose plus one section per top-level `## ` heading.
 *  Section titles come from the same heading text rehype-slug anchors when
 *  a section is rendered -- there is no separate hardcoded title list to
 *  keep in sync. */
function splitIntoSections(markdown: string): SplitResult {
  const firstHeadingAt = markdown.search(/^##\s+/m);
  if (firstHeadingAt === -1) {
    // No top-level heading found at all (malformed content) -- render it as
    // a single, unlabeled section rather than crash.
    return { preamble: "", sections: markdown.trim() ? [{ title: "", markdown }] : [] };
  }
  const preamble = markdown.slice(0, firstHeadingAt);
  const body = markdown.slice(firstHeadingAt);
  // Split right before every top-level heading; the body starts with one, so
  // the first chunk is never empty.
  const chunks = body.split(/\n(?=##\s+)/);
  const sections = chunks.map((chunk) => {
    const newlineAt = chunk.indexOf("\n");
    const headingLine = newlineAt === -1 ? chunk : chunk.slice(0, newlineAt);
    const match = TOP_HEADING_RE.exec(headingLine);
    return { title: match ? match[1] : "", markdown: chunk };
  });
  return { preamble, sections };
}

/** The manual's own first section is its "Table of contents" heading, which
 *  lists one `[title](#anchor)` link per *other* section, in the same order
 *  those sections appear. Those anchors are already exactly what rehype-slug
 *  assigns when the whole thing renders (that's how the flat single-page
 *  version's own table of contents worked) -- reusing them here, positionally,
 *  avoids a second hand-rolled slugify implementation just to answer "does
 *  `location.hash` point at one of our sections". Returns `[]` if the count
 *  doesn't line up with the actual section count, so a future manual edit
 *  that drifts the two out of sync degrades to "no deep-link match" instead
 *  of silently pointing at the wrong section. */
function tocAnchorsBySectionIndex(sections: ManualSection[]): (string | undefined)[] {
  if (sections.length === 0) return [];
  const anchors = [...sections[0].markdown.matchAll(/]\(#([^)]+)\)/g)].map((m) =>
    decodeURIComponent(m[1]),
  );
  if (anchors.length !== sections.length - 1) return [];
  return [undefined, ...anchors];
}

/** Renders the in-app user manual, fetched as a static Markdown asset per
 *  locale (public/user-manual/{en,ja}.md) rather than embedded in JSX. This
 *  keeps a long-form document out of the per-string i18n pipeline and out of
 *  lint:i18n-strings' kana check entirely -- the Japanese text lives in a
 *  .md asset, never in a .tsx source file, same reasoning that already
 *  exempts images.
 *
 *  The manual is split client-side (in memory, not a second file format) by
 *  its top-level `## ` headings into sections; a fixed left sidebar lists the
 *  section titles and only the selected section renders on the right.
 *
 *  Deep-link decision: the initial section is chosen from `location.hash`
 *  via the manual's own table-of-contents anchors (see
 *  `tocAnchorsBySectionIndex`), so old bookmarks/shared links from the flat-
 *  scrolling page (`#1-choosing-an-agency...` etc., one per top-level
 *  section) still land on the right section. This is NOT preserved for a
 *  `###` subsection anchor (e.g. `#5-3-something`): only top-level section
 *  anchors are matched, so a subsection link falls all the way back to
 *  `defaultIndex` below -- a different, unrelated section, not merely a lost
 *  disambiguating suffix. No current in-repo or external link targets a
 *  subsection anchor (checked both manuals' own cross-references), so this
 *  is a real but so-far-unexercised gap, not an active break. */
export function HelpPage() {
  const { t, i18n } = useTranslation();
  // Same fallback chain as api/client.ts's Accept-Language header, not the
  // raw (possibly still-detecting) i18n.language other call sites use.
  const resolved = i18n.resolvedLanguage ?? i18n.language ?? "ja";
  const locale = resolved.startsWith("en") ? "en" : "ja";

  const { data: content, error, refetch } = useQuery({
    queryKey: ["userManual", locale],
    queryFn: ({ signal }) => fetchManual(locale, signal),
  });

  // React Compiler memoizes derived values automatically -- these are plain
  // function calls, not useMemo, per repo convention.
  const { preamble, sections } =
    content == null ? { preamble: "", sections: [] } : splitIntoSections(stripLeadingH1(content));
  const tocAnchors = tocAnchorsBySectionIndex(sections);

  // `explicitIndex` is only ever set from a real event -- a sidebar click or
  // a matched `hashchange` -- each of which simply overwrites it, so the most
  // recent explicit action always wins. Before either has happened, the
  // section shown is *derived* (not stored) from `initialHash`, the page's
  // hash at first mount, so there's no effect synchronously setting state
  // from other reactive state (only legitimate external-event subscriptions
  // do that, in their callbacks, below).
  const [explicitIndex, setExplicitIndex] = useState<number | null>(null);
  const [initialHash] = useState(() =>
    typeof window === "undefined" ? "" : decodeURIComponent(window.location.hash.replace(/^#/, "")),
  );

  // Keeps in-content anchor links (e.g. the manual's own "Table of contents"
  // section links to `#5-analysis-tab...`) working even though the target
  // section isn't in the DOM yet when such a link is clicked: the browser
  // still updates location.hash and fires `hashchange`, which this picks up
  // to switch sections. Also covers back/forward through hash history.
  useEffect(() => {
    function onHashChange() {
      const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
      const idx = tocAnchors.findIndex((a) => a === hash);
      if (idx !== -1) setExplicitIndex(idx);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [tocAnchors]);

  const initialHashIndex = tocAnchors.findIndex((a) => a === initialHash);
  // When a "Table of contents" section exists (tocAnchors[0] is always
  // `undefined` in that case -- see tocAnchorsBySectionIndex), the sidebar
  // already serves the ToC's own navigation purpose, so the default view
  // (no hash, no sidebar click yet) should land on the first real section
  // instead of the ToC list of links.
  const defaultIndex = tocAnchors.length > 0 ? 1 : 0;
  const derivedIndex = explicitIndex ?? (initialHashIndex !== -1 ? initialHashIndex : defaultIndex);
  const safeIndex = sections.length === 0 ? 0 : Math.min(derivedIndex, sections.length - 1);
  const contentRef = useRef<HTMLDivElement>(null);

  // Keeps the address bar in sync with whichever section is actually on
  // screen, using the section's real rendered heading id (rehype-slug's own
  // output) rather than a second slug computation -- this covers both
  // sidebar clicks and the initial hash-driven selection above. replaceState
  // (not a real navigation) avoids spamming history with one entry per
  // section switch and doesn't itself fire `hashchange`.
  useEffect(() => {
    const heading = contentRef.current?.querySelector("h2[id]");
    if (heading?.id) window.history.replaceState(null, "", `#${heading.id}`);
    // sections.length also gates this: safeIndex can stay unchanged (e.g. 0
    // before content loads and 0 is also the eventual default) across the
    // loading -> loaded transition, so without it this effect would skip
    // re-running once the real heading exists in the DOM. content is needed
    // too: switching the UI language re-fetches a differently-worded manual
    // whose heading ids differ (rehype-slug slugs the translated text), even
    // when safeIndex/sections.length stay the same -- without it the address
    // bar would keep pointing at the previous locale's slug.
  }, [safeIndex, sections.length, content]);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 0 64px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("help.title")}</h1>
      {error != null && <ErrorBanner error={error} onRetry={() => void refetch()} />}
      {content == null && error == null && (
        <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>
      )}
      {/* Rendered above the sidebar, unconditionally, regardless of which
          section is selected -- reuses .user-manual-content for shared
          p/li/a styling only; it has no <h2> so that class's h2:first-child
          reset is simply inert here. */}
      {content != null && sections.length > 0 && preamble.trim() !== "" && (
        <div className="user-manual-content" style={{ marginBottom: 24 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preamble}</ReactMarkdown>
        </div>
      )}
      {content != null && sections.length > 0 && (
        <div style={{ display: "flex", gap: 32, alignItems: "flex-start" }}>
          <nav
            aria-label={t("help.sections_nav")}
            style={{ width: 240, flexShrink: 0, position: "sticky", top: 16 }}
          >
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {sections.map((section, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => setExplicitIndex(i)}
                    aria-current={i === safeIndex ? "true" : undefined}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      background: i === safeIndex ? "var(--accent-soft)" : "transparent",
                      border: "none",
                      borderLeft: `3px solid ${i === safeIndex ? "var(--accent)" : "transparent"}`,
                      color: "var(--text-primary)",
                      fontWeight: i === safeIndex ? 600 : 400,
                      fontSize: 13,
                      lineHeight: 1.4,
                      padding: "8px 12px",
                      cursor: "pointer",
                      borderRadius: "var(--radius)",
                    }}
                  >
                    {section.title}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <div className="user-manual-content" style={{ flex: 1, minWidth: 0 }} ref={contentRef}>
            <ReactMarkdown
              // GFM adds the table syntax the manual uses (plain CommonMark,
              // react-markdown's default, treats a pipe table as one text
              // paragraph). rehype-slug adds heading `id`s matching the
              // manual's own GitHub-style table-of-contents anchors.
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSlug]}
              components={{
                // Manual images are authored as relative paths (./NN-x.png)
                // so the source .md also renders correctly viewed directly
                // on GitHub -- rewrite only those to this page's actual
                // asset location; leave absolute/data URLs untouched.
                img: ({ src, alt, title }) => (
                  <img
                    src={
                      typeof src === "string" && src.startsWith("./")
                        ? `${MANUAL_BASE}/${src.slice(2)}`
                        : src
                    }
                    alt={alt}
                    title={title}
                  />
                ),
              }}
            >
              {sections[safeIndex].markdown}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
