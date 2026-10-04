/** A session's browser and system, read from its user-agent string, or null
 *  when the string names neither. Order matters: Edge and Opera also claim
 *  Chrome, Chrome also claims Safari, and iOS claims "like Mac OS X". */
export function describeUserAgent(ua: string | null | undefined): { browser: string; os: string } | null {
  if (!ua) return null;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Version\/.*Safari\//.test(ua)
            ? "Safari"
            : null;
  const os = /iPhone|iPad/.test(ua)
    ? "iOS"
    : /Android/.test(ua)
      ? "Android"
      : /CrOS/.test(ua)
        ? "ChromeOS"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Windows/.test(ua)
            ? "Windows"
            : /Linux/.test(ua)
              ? "Linux"
              : null;
  return browser && os ? { browser, os } : null;
}
