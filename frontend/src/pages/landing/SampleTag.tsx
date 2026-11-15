import { useTranslation } from "react-i18next";

/** The one visible "this is sample data" label (decision: every figure, table,
 *  chart and map on /welcome is a static fixture, never a live API read, and
 *  stays visibly marked as such -- a signed-out visitor never sees real
 *  data). Reused next to every figure group the page draws. */
export function SampleTag({ className }: { className?: string }) {
  const { t } = useTranslation();
  return <span className={`landing-tag landing-sample-tag${className ? ` ${className}` : ""}`}>{t("landing.sample_tag")}</span>;
}
