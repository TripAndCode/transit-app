/** Monday-first day-of-week keys, indexed `dow - 1` to match the pipeline's
 *  1=Monday..7=Sunday `dow` convention. Used to build `forecast.dow_*` i18n
 *  keys. */
export const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
