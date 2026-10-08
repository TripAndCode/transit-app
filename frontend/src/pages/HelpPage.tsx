import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import { ApiError } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";
import { SidebarNavList } from "../components/SidebarNavList";
import { PageHeader } from "../components/ui/PageHeader";
import { coalesceToFrame } from "../utils/frameCoalesce";

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
  /** Raw heading text, e.g. `5. Routes — which routes run late`, used
   *  verbatim as the sidebar label. */
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
 *  keep in sync.
 *
 *  The `## `-boundary detection is a plain line-anchored regex, not fence-
 *  aware -- literal `## `-shaped text inside a fenced/indented code block or
 *  blockquote would be mis-split into a bogus section. Neither manual
 *  contains a code fence today (verified against both `public/user-manual/
 *  {en,ja}.md`), so this is dormant, not an active break; a future manual
 *  edit demonstrating Markdown heading syntax in a code sample would need to
 *  either avoid `## ` at fence-column-0 or this function would need to skip
 *  fenced regions first. */
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

/** Whether the manual's first section is its own "Table of contents": a
 *  list of one `[title](#anchor)` link per other section. That list serves
 *  the source file read on its own; on this page the sidebar is the one
 *  table of contents, so the section is left out. A list that does not line
 *  up with the sections reads as an ordinary section rather than being
 *  dropped. */
function hasOwnContents(sections: ManualSection[]): boolean {
  // A lone section has nothing else a contents list could point to.
  if (sections.length < 2) return false;
  return [...sections[0].markdown.matchAll(/]\(#([^)]+)\)/g)].length === sections.length - 1;
}

/** How long a contents click waits for the browser's `scrollend` before it
 *  lets the scroll position decide the current section again; browsers
 *  without `scrollend`, and clicks that need no scroll, never send one. */
const SCROLL_SETTLE_MS = 1000;

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** The scrolled element behind a scroll event: the page's scroller for the
 *  document itself, else the element (the app shell scrolls its main pane). */
function scrollerOf(target: EventTarget | null): Element | null {
  if (target instanceof Element) return target;
  return document.scrollingElement ?? document.documentElement;
}

/** Renders the in-app user manual, fetched as a static Markdown asset per
 *  locale (public/user-manual/{en,ja}.md) rather than embedded in JSX. This
 *  keeps a long-form document out of the per-string i18n pipeline and out of
 *  lint:i18n-strings' kana check entirely -- the Japanese text lives in a
 *  .md asset, never in a .tsx source file, same reasoning that already
 *  exempts images.
 *
 *  The manual reads as one document. The sidebar lists its top-level
 *  sections and marks the one being read: the last section whose top has
 *  passed the upper third of the screen, or the last section once the page
 *  can scroll no further. A sidebar click scrolls to its section, smoothly
 *  unless the reader asks for reduced motion, and keeps that entry marked
 *  until the scroll ends rather than stepping through every section passed
 *  on the way. The page opens at its title; only a `#heading` in the address
 *  opens it further down. */
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
  const navIndices = sections.map((_, i) => i).filter((i) => !(i === 0 && hasOwnContents(sections)));

  const [searchQuery, setSearchQuery] = useState("");
  const [current, setCurrent] = useState<number | null>(null);
  const documentRef = useRef<HTMLDivElement>(null);
  // Set while a sidebar click's scroll runs, so the sections it passes on
  // the way don't take the mark from the entry that was clicked.
  const settlingRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Client-side filter over the loaded manual's own sections -- title and
  // body text, in whichever locale is currently fetched (see `fetchManual`
  // above; only one locale's markdown is ever in memory at a time).
  const normalizedQuery = searchQuery.trim().toLowerCase();
  const isSearching = normalizedQuery !== "";
  const matchedIndices = isSearching
    ? navIndices.filter(
        (i) =>
          sections[i].title.toLowerCase().includes(normalizedQuery) ||
          sections[i].markdown.toLowerCase().includes(normalizedQuery),
      )
    : navIndices;
  const hasMatches = matchedIndices.length > 0;
  const activeIndex = current != null && matchedIndices.includes(current) ? current : (matchedIndices[0] ?? null);

  function sectionElements(): HTMLElement[] {
    return [...(documentRef.current?.querySelectorAll<HTMLElement>("[data-section]") ?? [])];
  }

  function stopSettling() {
    if (settlingRef.current != null) clearTimeout(settlingRef.current);
    settlingRef.current = null;
  }

  function goToSection(i: number) {
    setCurrent(i);
    const section = sectionElements().find((el) => Number(el.dataset.section) === i);
    if (!section) return;
    stopSettling();
    settlingRef.current = setTimeout(stopSettling, SCROLL_SETTLE_MS);
    section.scrollIntoView?.({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" }); // jsdom has no scrollIntoView
    const heading = section.querySelector("h2[id]");
    if (heading?.id) window.history.replaceState(null, "", `#${heading.id}`);
  }

  const followScroll = useEffectEvent((target: EventTarget | null) => {
    if (settlingRef.current != null) return;
    const els = sectionElements();
    if (els.length === 0) return;
    const scroller = scrollerOf(target);
    const atEnd =
      scroller != null &&
      scroller.scrollHeight > scroller.clientHeight &&
      scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
    const line = window.innerHeight / 3;
    const passed = els.filter((el) => el.getBoundingClientRect().top <= line);
    const el = atEnd ? els[els.length - 1] : (passed[passed.length - 1] ?? els[0]);
    setCurrent(Number(el.dataset.section));
  });
  useEffect(() => {
    let target: EventTarget | null = null;
    const frame = coalesceToFrame(() => followScroll(target));
    const onScroll = (e: Event) => {
      target = e.target;
      frame.schedule();
    };
    const onScrollEnd = () => stopSettling();
    // Capturing on the document hears the app shell's scrolling pane as
    // well as the page itself; scroll events do not bubble.
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    document.addEventListener("scrollend", onScrollEnd, { capture: true });
    return () => {
      frame.cancel();
      document.removeEventListener("scroll", onScroll, { capture: true });
      document.removeEventListener("scrollend", onScrollEnd, { capture: true });
      stopSettling();
    };
  }, []);

  // A `#heading` in the address at opening is a deep link: take the reader
  // there once the manual has rendered. Without one the page stays at its
  // title.
  const openedAtHashRef = useRef(false);
  useEffect(() => {
    if (content == null || openedAtHashRef.current) return;
    openedAtHashRef.current = true;
    const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    if (!hash) return;
    const target = document.getElementById(hash);
    if (target && documentRef.current?.contains(target)) target.scrollIntoView?.({ block: "start" });
  }, [content]);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 0 64px" }}>
      <PageHeader title={t("help.title")} />
      <input
        type="search"
        placeholder={t("help.search_placeholder")}
        aria-label={t("help.search_placeholder")}
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        style={{
          width: "100%",
          padding: "11px 14px",
          fontSize: 13,
          border: "1px solid var(--card-border)",
          borderRadius: "var(--card-radius)",
          background: "var(--bg-soft)",
          color: "var(--text-primary)",
          marginBottom: isSearching ? 6 : 18,
        }}
      />
      {/* Visible feedback doubles as the aria-live announcement -- a single
          role="status" region rather than a separate visually-hidden one,
          since the count is useful to a sighted user too. Only shown while
          actively searching, so the calm default view stays uncluttered. */}
      {isSearching && (
        <div role="status" aria-live="polite" style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 18 }}>
          {t("help.search_result_count", { count: matchedIndices.length })}
        </div>
      )}
      {error != null && <ErrorBanner error={error} onRetry={() => void refetch()} />}
      {content == null && error == null && (
        <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>
      )}
      {/* Reuses .user-manual-content for shared p/li/a styling only. */}
      {content != null && sections.length > 0 && preamble.trim() !== "" && (
        <div className="user-manual-content" style={{ marginBottom: 24 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preamble}</ReactMarkdown>
        </div>
      )}
      {content != null && sections.length > 0 && (
        <div style={{ display: "flex", gap: 32, alignItems: "flex-start" }}>
          <SidebarNavList
            ariaLabel={t("help.sections_nav")}
            width={240}
            navStyle={{ position: "sticky", top: 16 }}
            items={matchedIndices.map((i) => ({ key: i, label: sections[i].title }))}
            activeKey={hasMatches ? activeIndex : null}
            onSelect={goToSection}
          />
          {!hasMatches && (
            <div style={{ flex: 1, minWidth: 0, color: "var(--text-tertiary)" }}>{t("common.no_match")}</div>
          )}
          {hasMatches && (
            <div ref={documentRef} style={{ flex: 1, minWidth: 0 }}>
              {matchedIndices.map((i) => (
                <section key={i} data-section={i} className="user-manual-content user-manual-section">
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
                          src={typeof src === "string" && src.startsWith("./") ? `${MANUAL_BASE}/${src.slice(2)}` : src}
                          alt={alt}
                          title={title}
                        />
                      ),
                    }}
                  >
                    {sections[i].markdown}
                  </ReactMarkdown>
                </section>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
