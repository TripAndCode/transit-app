import type { TFunction } from "i18next";
import type { BoardAlert } from "../../api/admin";

/** A board alert in the reader's language, the same on the board and in the
 *  header's alert center. Plural forms are picked by `count`; a stale-agency
 *  alert carries its lag as `days`. The server's English text is the
 *  fallback for a code with no translation. */
export function alertText(t: TFunction, alert: BoardAlert): string {
  const { params } = alert;
  return t(`admin.board.alert.${alert.code}`, { ...params, count: params.count ?? params.days, defaultValue: alert.text });
}
