import { useEffect, useEffectEvent, useRef, type ReactNode, type RefObject } from "react";
import { useTopmostEscape } from "../../hooks/useFocusTrap";
import { popoverLeft } from "./popoverPosition";

/** A condition's popover. Focus moves in on open. Escape closes it and hands
 *  focus back to the token, through the shared Escape stack so an overlay
 *  opened over it takes the Escape instead. A click outside also closes it;
 *  focus leaving it is handled by the token's wrapper.
 *
 *  It is positioned against the scope section rather than its token, so a
 *  token near the right edge opens a popover that still fits the screen. */
export function ScopePopover({
  label,
  onClose,
  returnFocusTo,
  children,
}: {
  label: string;
  onClose: () => void;
  returnFocusTo: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useEffectEvent(onClose);

  useTopmostEscape(true, () => {
    onClose();
    returnFocusTo.current?.focus();
  });

  useEffect(() => {
    const node = ref.current;
    const anchor = returnFocusTo.current;
    const box = node?.offsetParent;
    if (node && anchor && box) {
      const a = anchor.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      node.style.top = `${a.bottom - b.top + 6}px`;
      node.style.left = `${popoverLeft(a.left - b.left, node.offsetWidth, b.width)}px`;
    }
    node?.querySelector<HTMLElement>("button, input, select, textarea")?.focus();
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node;
      if (ref.current?.contains(target) || returnFocusTo.current?.contains(target)) return;
      close();
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [returnFocusTo]);

  return (
    <div ref={ref} role="dialog" aria-label={label} tabIndex={-1} className="scope-popover">
      {children}
    </div>
  );
}
