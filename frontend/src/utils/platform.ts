/** A subset of `Navigator` this module reads, so a test can pass a fake one
 *  without touching the real `navigator` global. `userAgentData.platform` is
 *  the current standard on Chromium; `platform` is the deprecated fallback
 *  every other engine still reports. */
type PlatformSource = {
  userAgentData?: { platform?: string };
  platform?: string;
};

const APPLE_PLATFORM = /mac|iphone|ipad|ipod/i;

/**
 * The keyboard modifier label to show for shortcuts: `⌘` on macOS/iOS/iPadOS,
 * `Ctrl` everywhere else (Windows, Linux, ChromeOS, Android).
 */
export function modifierKeyLabel(nav: PlatformSource = navigator): string {
  const platform = nav.userAgentData?.platform ?? nav.platform ?? "";
  return APPLE_PLATFORM.test(platform) ? "⌘" : "Ctrl";
}
