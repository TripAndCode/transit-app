import { useId, type CSSProperties, type ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";
import { contentLocale } from "../i18n/contentLocale";
import { DATA_SOURCES } from "./dataSources";

const PARAGRAPH: CSSProperties = {
  margin: "4px 0 0",
  fontSize: "var(--text-xs)",
  color: "var(--text-tertiary)",
  lineHeight: 1.6,
};

/** The open data behind every figure, credited where a reader can see it:
 *  one line per entry of `DATA_SOURCES`, which says why each is worded and
 *  linked as it is. */
export function DataSourceCredits({ className, style }: { className?: string; style?: CSSProperties }) {
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const locale = contentLocale(i18n);

  return (
    <section className={className} style={style} aria-labelledby={headingId}>
      <h2
        id={headingId}
        style={{ margin: 0, fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-secondary)" }}
      >
        {t("credits.title")}
      </h2>
      {DATA_SOURCES.map((source) => (
        <p key={source.creditKey} style={PARAGRAPH}>
          <Trans
            t={t}
            i18nKey={source.creditKey}
            components={Object.fromEntries(
              Object.entries(source.links(locale)).map(([tag, href]) => [tag, <SourceLink key={tag} href={href} />]),
            )}
          />
        </p>
      ))}
    </section>
  );
}

/** Trans fills `children` with the link text from the translation. */
function SourceLink({ href, children }: { href: string; children?: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}
