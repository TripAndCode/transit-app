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

/** Renders the in-app user manual, fetched as a static Markdown asset per
 *  locale (public/user-manual/{en,ja}.md) rather than embedded in JSX. This
 *  keeps a long-form document out of the per-string i18n pipeline and out of
 *  lint:i18n-strings' kana check entirely -- the Japanese text lives in a
 *  .md asset, never in a .tsx source file, same reasoning that already
 *  exempts images. */
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

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "0 0 64px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("help.title")}</h1>
      {error != null && <ErrorBanner error={error} onRetry={() => void refetch()} />}
      {content == null && error == null && (
        <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>
      )}
      {content != null && (
        <div className="user-manual-content">
          <ReactMarkdown
            // GFM adds the table syntax the manual uses (plain CommonMark,
            // react-markdown's default, treats a pipe table as one text
            // paragraph). rehype-slug adds heading `id`s matching the
            // manual's own GitHub-style table-of-contents anchors.
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSlug]}
            components={{
              // Manual images are authored as relative paths (./NN-x.png) so
              // the source .md also renders correctly viewed directly on
              // GitHub -- rewrite only those to this page's actual asset
              // location; leave absolute/data URLs untouched.
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
            {stripLeadingH1(content)}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
