/** True for an element that consumes plain-letter keystrokes as text input,
 *  so a single-key shortcut (e.g. `/`, `j`/`k`, `x`/`a`) doesn't fire while
 *  the user is typing into it. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}
