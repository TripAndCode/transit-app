import { useTranslation } from "react-i18next";
import { serviceLabel } from "../utils/filterValueLabels";

const JAPANESE = /[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}]/u;

/** A service's name in the UI's language. An agency's own name with no
 *  translation (お盆臨時 20日) is kept as written and marked as Japanese, so a
 *  screen reader on an English screen reads it as Japanese. */ // i18n-ignore: JSDoc example
export function ServiceName({ value }: { value: string }) {
  const { t, i18n } = useTranslation();
  const { text, translated } = serviceLabel(value, t);
  if (translated || i18n.resolvedLanguage === "ja" || !JAPANESE.test(text)) return <>{text}</>;
  return <span lang="ja">{text}</span>;
}
