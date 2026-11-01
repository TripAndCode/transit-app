import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";
import { contentLocale } from "../i18n/contentLocale";
import { creditText, dataSourceOf } from "./dataSources";

/** The credit for the open data behind an agency's figures, as plain text for
 *  paper or a CSV cell: only the source its feed is published under. Null
 *  outside an agency, while the agency list loads, or when no source covers
 *  the feed. */
export function useAgencyCredit(agencyId: number | null): string | null {
  const { t, i18n } = useTranslation();
  const feedUrl = useAgencies().data?.find((a) => a.agency_id === agencyId)?.feed_url;
  const source = feedUrl == null ? null : dataSourceOf(feedUrl);
  return source == null ? null : creditText(source, t, contentLocale(i18n));
}
