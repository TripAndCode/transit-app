import { useQuery } from "@tanstack/react-query";

const MANUAL_BASE = "/user-manual";

export type Locale = "en" | "ja";
export type TabManualKey = "overview" | "map" | "analysis" | "network" | "live" | "ask";

// The user manual (public/user-manual/{en,ja}.md) numbers its top-level
// sections 1-9 in the same order in both locales --
// tests/frontend/user_manual_parity.test.mjs enforces identical (#/##/###)
// heading counts between them. Matching by that stable number, rather than
// locale-specific heading text, means this mapping doesn't need an
// English *and* Japanese pattern per tab.
const HEADING_NUMBER: Record<TabManualKey, number> = {
  overview: 3,
  map: 4,
  analysis: 5,
  network: 6,
  live: 7,
  ask: 8,
};

export async function fetchManualText(locale: Locale, signal?: AbortSignal): Promise<string> {
  const r = await fetch(`${MANUAL_BASE}/${locale}.md`, { signal });
  if (!r.ok) {
    throw new Error(`Failed to load user manual (${r.status})`);
  }
  return r.text();
}

/** Slices out the body of the `## <n>. ...` section for the given heading
 *  number: everything after that heading line up to (exclusive of) the
 *  next `## ` heading, or end of document. Returns null if the manual has
 *  no heading with that number -- a manual edit that renumbers sections
 *  degrades the landing page's excerpt to "unavailable" rather than
 *  crashing it. */
export function extractSection(markdown: string, headingNumber: number): string | null {
  const headingRe = new RegExp(`^##\\s+${headingNumber}\\.`, "m");
  const match = headingRe.exec(markdown);
  if (!match) return null;
  const headingLineEnd = markdown.indexOf("\n", match.index);
  const bodyStart = headingLineEnd === -1 ? markdown.length : headingLineEnd + 1;
  const rest = markdown.slice(bodyStart);
  const nextHeading = /\n##\s+\d/.exec(rest);
  return (nextHeading ? rest.slice(0, nextHeading.index) : rest).trim();
}

/** The section's first prose paragraph: skips leading blank lines and
 *  Markdown image references (the manual always opens a section with a
 *  screenshot), then collects lines up to the next blank line. Bold
 *  markers are stripped for plain-text display -- this is a short excerpt,
 *  not a rendered Markdown document. */
export function firstParagraph(sectionBody: string): string {
  const collected: string[] = [];
  let started = false;
  for (const rawLine of sectionBody.split("\n")) {
    const line = rawLine.trim();
    if (!started) {
      if (line === "" || line.startsWith("![")) continue;
      started = true;
    }
    if (line === "") break;
    collected.push(line);
  }
  return collected.join(" ").replace(/\*\*/g, "");
}

export function manualExcerptFor(markdown: string, tab: TabManualKey): string | null {
  const section = extractSection(markdown, HEADING_NUMBER[tab]);
  if (section == null) return null;
  const paragraph = firstParagraph(section);
  return paragraph === "" ? null : paragraph;
}

/** Fetches the user manual for `locale` (cached indefinitely per locale --
 *  it's a static build asset, not data that changes within a session) and
 *  returns the excerpt for `tab`. */
export function useManualExcerpt(locale: Locale, tab: TabManualKey) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["landingManualExcerpt", locale],
    queryFn: ({ signal }) => fetchManualText(locale, signal),
    staleTime: Infinity,
  });
  const excerpt = data != null ? manualExcerptFor(data, tab) : null;
  return { excerpt, isLoading, isError };
}
