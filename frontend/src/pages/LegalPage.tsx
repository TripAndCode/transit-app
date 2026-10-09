import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";
import { LanguageToggle } from "../components/LanguageToggle";
import { useDocumentLocale } from "../i18n/useDocumentLocale";
import "./LegalPage.css";

type LegalDoc = "privacy" | "terms";

async function fetchLegalDoc(doc: LegalDoc, locale: string, signal: AbortSignal): Promise<string> {
  const r = await fetch(`/legal/${doc}.${locale}.md`, { signal });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new ApiError(r.status, body);
  }
  return r.text();
}

/** The privacy policy or the terms of service, fetched as a static Markdown
 *  asset per locale (public/legal/) so a visitor can read it before signing
 *  in. The file keeps its own `# Title` for reading on GitHub; the page drops
 *  it in favour of its own heading. */
export function LegalPage({ doc }: { doc: LegalDoc }) {
  const { t, i18n } = useTranslation();
  useDocumentLocale();
  const resolved = i18n.resolvedLanguage ?? i18n.language ?? "ja";
  const locale = resolved.startsWith("en") ? "en" : "ja";
  const { data, error, refetch } = useQuery({
    queryKey: ["legalDoc", doc, locale],
    queryFn: ({ signal }) => fetchLegalDoc(doc, locale, signal),
    staleTime: Infinity,
  });

  return (
    <div className="legal-page">
      <header className="legal-page__header">
        <Link to="/welcome">
          <span aria-hidden="true">← </span>
          {t("header.app_title")}
        </Link>
        <LanguageToggle />
      </header>
      <main className="legal-page__main">
        <h1 className="legal-page__title">{doc === "privacy" ? t("legal.privacy") : t("legal.terms")}</h1>
        {data == null && error == null && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}
        {error != null && <ErrorBanner error={error} onRetry={() => void refetch()} />}
        {data != null && (
          <article className="user-manual-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.replace(/^#\s.*\n+/, "")}</ReactMarkdown>
          </article>
        )}
      </main>
    </div>
  );
}
