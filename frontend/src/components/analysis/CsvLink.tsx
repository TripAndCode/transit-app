import { useTranslation } from "react-i18next";
import { contentLocale } from "../../i18n/contentLocale";

/** A report's CSV download from the API, beside its heading. A plain link
 *  sends the browser's Accept-Language rather than the UI's, so the link names
 *  the UI language in `lang`, which the file's data-source credit is written in. */
export function CsvLink({ href }: { href: string }) {
  const { t, i18n } = useTranslation();
  return (
    <a
      href={`${href}${href.includes("?") ? "&" : "?"}lang=${contentLocale(i18n)}`}
      download
      style={{
        fontSize: 12,
        padding: "4px 12px",
        background: "transparent",
        border: "1px solid var(--border-subtle)",
        borderRadius: 4,
        color: "var(--text-secondary)",
        textDecoration: "none",
      }}
    >
      <span aria-hidden="true">⬇ </span>
      {t("reports.download_csv")}
    </a>
  );
}
