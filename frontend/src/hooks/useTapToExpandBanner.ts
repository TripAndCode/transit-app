import { useState, type CSSProperties, type KeyboardEvent } from "react";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "./useMediaQuery";
import { onActivateKey } from "../utils/a11y";

type MessageProps = {
  role?: "button";
  tabIndex?: number;
  "aria-expanded"?: boolean;
  onClick?: () => void;
  onKeyDown?: (e: KeyboardEvent) => void;
  style: CSSProperties;
};

/**
 * Shared "single line on mobile, tap to expand" behavior for the app's
 * persistent top banners (DataStalenessBanner, FeedHealthBanner,
 * GuestPrompt). Below the shared 640px breakpoint these otherwise each cost a
 * fixed per-banner height even though the wrapped message rarely needs more
 * than one line once actually read — with up to three stacked at once (item
 * 19's banner-hierarchy work), that adds up disproportionately on a phone
 * viewport. Desktop is unaffected: always the full, never-truncated message.
 *
 * A real toggle: the row stays a keyboard- and screen-reader-reachable
 * control (role="button", tabIndex, aria-expanded reflecting the actual
 * state) both before and after expanding, so a user can collapse it back to
 * the single-line form the same way they expanded it.
 */
export function useTapToExpandBanner(): { messageProps: MessageProps } {
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  const [expanded, setExpanded] = useState(false);

  if (!isMobile) {
    return { messageProps: { style: { flex: 1 } } };
  }

  function toggle() {
    setExpanded((prev) => !prev);
  }

  return {
    messageProps: {
      role: "button",
      tabIndex: 0,
      "aria-expanded": expanded,
      onClick: toggle,
      onKeyDown: onActivateKey(toggle),
      style: expanded
        ? { flex: 1, cursor: "pointer" }
        : {
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: "pointer",
          },
    },
  };
}
