/** The open menu's items, in DOM order. Read off the panel rather than kept
 *  in a ref array, so a menu whose items are conditional never has to hold a
 *  parallel list of nodes in sync with what it actually rendered. */
export function menuItems(panel: HTMLElement | null): HTMLElement[] {
  if (!panel) return [];
  return [...panel.querySelectorAll<HTMLElement>('[role="menuitem"]')];
}

/**
 * The item an arrow/Home/End keystroke should move focus to, or `null` when
 * the key is not one the menu owns and the event must be left alone.
 *
 * Both arrows wrap: a menu is a closed ring in the WAI-ARIA menu pattern,
 * and stopping at the ends puts the last item of a short list two keystrokes
 * further away than it needs to be. Focus that is outside the menu enters at
 * the near end -- first item for Down, last for Up.
 */
export function nextMenuItem(items: HTMLElement[], current: Element | null, key: string): HTMLElement | null {
  if (items.length === 0) return null;
  const index = current instanceof HTMLElement ? items.indexOf(current) : -1;
  switch (key) {
    case "ArrowDown":
      return index === -1 ? items[0] : items[(index + 1) % items.length];
    case "ArrowUp":
      return index === -1 ? items[items.length - 1] : items[(index - 1 + items.length) % items.length];
    case "Home":
      return items[0];
    case "End":
      return items[items.length - 1];
    default:
      return null;
  }
}
