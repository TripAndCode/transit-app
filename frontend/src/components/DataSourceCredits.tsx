import { useId, type CSSProperties, type ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";

/** The open data behind every figure, credited where a reader can see it.
 *
 *  - Aomori City Bus publishes its GTFS and GTFS-RT under CC BY 4.0 (its
 *    ckan.odpt.org dataset pages). The licence requires naming the provider,
 *    linking the material and the licence, and saying the data was modified;
 *    the wording follows ODPT's own template for modified CC BY material.
 *  - The Hiroshima Bus Association publishes its operators' feeds under CC0,
 *    which requires no credit; it is credited anyway. Its site asks visitors
 *    to link only its top page, which links on to the GTFS page.
 *
 *  public/legal/terms.{ja,en}.md repeats these sentences verbatim
 *  (legalDocs.test.ts holds the two together), and an agency seeded from a
 *  host these credits don't cover fails DataSourceCredits.test.tsx. */
export function DataSourceCredits({ className, style }: { className?: string; style?: CSSProperties }) {
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const resolved = i18n.resolvedLanguage ?? i18n.language ?? "ja";
  const deed = resolved.startsWith("en") ? "en" : "ja";
  const paragraph = { margin: "4px 0 0", fontSize: "var(--text-xs)", color: "var(--text-tertiary)", lineHeight: 1.6 };

  return (
    <section className={className} style={style} aria-labelledby={headingId}>
      <h2
        id={headingId}
        style={{ margin: 0, fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-secondary)" }}
      >
        {t("credits.title")}
      </h2>
      <p style={paragraph}>
        <Trans
          t={t}
          i18nKey="credits.aomori"
          components={{
            static: <SourceLink href="https://ckan.odpt.org/dataset/aomori_city_all_lines" />,
            realtime: <SourceLink href="https://ckan.odpt.org/dataset/odpt_aomori_city_all_lines" />,
            license: <SourceLink href={`https://creativecommons.org/licenses/by/4.0/deed.${deed}`} />,
          }}
        />
      </p>
      <p style={paragraph}>
        <Trans
          t={t}
          i18nKey="credits.hiroshima"
          components={{
            site: <SourceLink href="https://www.bus-kyo.or.jp/" />,
            license: <SourceLink href={`https://creativecommons.org/publicdomain/zero/1.0/deed.${deed}`} />,
          }}
        />
      </p>
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
