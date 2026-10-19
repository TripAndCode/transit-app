import { ActivityStrip } from "./ActivityStrip";
import { DataStalenessBanner } from "./DataStalenessBanner";
import { FeedHealthBanner } from "./FeedHealthBanner";

/**
 * The notices above the routed page: the data-staleness and feed-health
 * banners, then the activity strip. Scoped to the content area, not the whole
 * app shell: they are about the agency data being viewed, not app-wide chrome,
 * so they don't span above the sidebar.
 *
 * A focused tab hides the banners rather than unmounting them, so their query
 * and clock keep their state across a visit to one.
 */
export function NoticeStack({ focused }: { focused: boolean }) {
  return (
    <div className="app-notice-stack">
      <DataStalenessBanner hidden={focused} />
      <FeedHealthBanner hidden={focused} />
      <ActivityStrip />
    </div>
  );
}
