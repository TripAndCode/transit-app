import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import rehypeSlug from "rehype-slug";
import { ErrorBanner } from "../components/ErrorBanner";

const MANUAL_BASE = "/user-manual";

/** Renders the in-app user manual, fetched as a static Markdown asset per
 *  locale (public/user-manual/{en,ja}.md) rather than embedded in JSX. This
 *  keeps a long-form document out of the per-string i18n pipeline and out of
 *  lint:i18n-strings' kana check entirely -- the Japanese text lives in a
 *  .md asset, never in a .tsx source file, same reasoning that already
 *  exempts images. */
type FetchResult = { locale: string; content: string } | { locale: string; error: unknown };

export function HelpPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language.startsWith("en") ? "en" : "ja";
  // Keyed by the locale it was fetched for, so a stale result from a
  // just-superseded locale is never rendered -- derived below instead of
  // clearing state synchronously at the top of the effect (which would
  // trigger a cascading extra render on every locale change).
  const [result, setResult] = useState<FetchResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${MANUAL_BASE}/${locale}.md`)
      .then((r) => {
        if (!r.ok) throw new Error(`manual fetch failed: ${r.status}`);
        return r.text();
      })
      .then((content) => {
        if (!cancelled) setResult({ locale, content });
      })
      .catch((error: unknown) => {
        if (!cancelled) setResult({ locale, error });
      });
    return () => {
      cancelled = true;
    };
  }, [locale]);

  const current = result?.locale === locale ? result : null;
  const content = current && "content" in current ? current.content : null;
  const error = current && "error" in current ? current.error : null;

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "0 0 64px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("help.title")}</h1>
      {error != null && <ErrorBanner error={error} />}
      {content == null && error == null && (
        <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>
      )}
      {content != null && (
        <div className="user-manual-content">
          <ReactMarkdown
            // The manual's own table of contents links to GitHub-style
            // heading slugs (e.g. #5-analysis-tab--...); rehype-slug adds
            // matching `id`s to headings so those links actually scroll.
            rehypePlugins={[rehypeSlug]}
            components={{
              // Manual images are authored as relative paths (./NN-x.png) so
              // the source .md also renders correctly viewed directly on
              // GitHub -- rewrite them to this page's actual asset location.
              img: ({ src, alt }) => (
                <img
                  src={typeof src === "string" ? `${MANUAL_BASE}/${src.replace(/^\.\//, "")}` : src}
                  alt={alt}
                />
              ),
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
