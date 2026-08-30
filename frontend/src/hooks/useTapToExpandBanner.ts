import { useState, type CSSProperties, type KeyboardEvent } from "react";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "./useMediaQuery";

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
 * Deliberately one-directional (compact -> expanded, not a collapse-back
 * toggle) — once a user has tapped through to read the full message there's
 * no value in re-truncating it later in the same visit, and a toggle would
 * add aria-expanded/keyboard-interaction complexity for no real benefit.
 */
export function useTapToExpandBanner(): { messageProps: MessageProps } {
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  const [expanded, setExpanded] = useState(false);
  const compact = isMobile && !expanded;

  if (!compact) {
    return { messageProps: { style: { flex: 1 } } };
  }

  function expand() {
    setExpanded(true);
  }

  return {
    messageProps: {
      role: "button",
      tabIndex: 0,
      "aria-expanded": false,
      onClick: expand,
      onKeyDown: (e: KeyboardEvent) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          expand();
        }
      },
      style: {
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
