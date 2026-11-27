import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeSlug from "rehype-slug";
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

/** The heading ids the manual gets with HelpPage's own slug plugin. */
function headingIds(locale: "en" | "ja"): string[] {
  const markdown = readFileSync(path.resolve(__dirname, `../../public/user-manual/${locale}.md`), "utf8");
  const html = renderToStaticMarkup(<ReactMarkdown rehypePlugins={[rehypeSlug]}>{markdown}</ReactMarkdown>);
  return [...html.matchAll(/<h[1-6] id="([^"]+)"/g)].map((m) => m[1]);
}

describe("help.anchor", () => {
  it.each([
    ["en", en.help.anchor],
    ["ja", ja.help.anchor],
  ] as const)("names a heading of the %s manual for every anchor", (locale, anchors) => {
    const ids = headingIds(locale);
    for (const anchor of Object.values(anchors)) expect(ids).toContain(anchor);
  });
});
