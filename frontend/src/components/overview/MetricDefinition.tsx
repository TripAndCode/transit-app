import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { InsightHint } from "../InsightHint";

/** The user manual's sections a definition can point to. Each anchor is the
 *  heading's slug in that locale's manual, kept in the locale files beside
 *  the copy (`help.anchor.*`), since the two manuals' headings differ. */
type ManualSection = "pulse" | "delay_figures";

/**
 * A headline figure's definition, in an ⓘ popover beside it: one or two
 * sentences consistent with the user manual, then a link to the manual's
 * section as the secondary "more". The definition itself never needs the
 * link, so the reader stays on the page.
 */
export function MetricDefinition({ title, body, manual }: { title: string; body: string; manual: ManualSection }) {
  const { t } = useTranslation();
  return (
    <InsightHint
      title={title}
      body={
        <>
          <span style={{ display: "block" }}>{body}</span>
          <Link
            to={`/help#${t(`help.anchor.${manual}`)}`}
            style={{ display: "inline-block", marginTop: 6, color: "var(--accent)", fontSize: 12 }}
          >
            {t("overview.definition.more")}
          </Link>
        </>
      }
    />
  );
}
