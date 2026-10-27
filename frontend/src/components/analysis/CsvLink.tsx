import { useTranslation } from "react-i18next";

/** A report's CSV download, beside its heading. */
export function CsvLink({ href }: { href: string }) {
  const { t } = useTranslation();
  return (
    <a
      href={href}
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
