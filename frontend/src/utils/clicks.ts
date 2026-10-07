/** A click the anchor would handle as a same-tab navigation. Modifier keys
 *  and other buttons (new tab, new window) stay the browser's. */
export function isPlainLeftClick(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}
