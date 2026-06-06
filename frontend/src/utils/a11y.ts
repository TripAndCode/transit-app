import type { KeyboardEvent } from "react";

/**
 * Returns an `onKeyDown` handler that invokes `activate` when the user presses
 * Enter or Space — mirroring native button activation. Use on elements that
 * carry `role="button"` + `tabIndex={0}` so click-only handlers also work for
 * keyboard users, without changing the element's visual identity.
 *
 * Space is prevented from scrolling the page (its default action) when the
 * element is focused, matching native `<button>` behaviour.
 */
export function onActivateKey(
  activate: (e: KeyboardEvent<Element>) => void,
): (e: KeyboardEvent<Element>) => void {
  return (e) => {
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      activate(e);
    }
  };
}
