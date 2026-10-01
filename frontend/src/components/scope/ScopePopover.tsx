import { useEffect, useEffectEvent, useRef, type ReactNode, type RefObject } from "react";

/** A condition's popover: focus moves in on open, and Escape or a click
 *  outside closes it. Escape hands focus back to the token that opened it. */
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
  const refocus = useEffectEvent(() => returnFocusTo.current?.focus());

  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button, input, select, textarea, [tabindex]")?.focus();
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node;
      if (ref.current?.contains(target) || returnFocusTo.current?.contains(target)) return;
      close();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      close();
      refocus();
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [returnFocusTo]);

  return (
    <div ref={ref} role="dialog" aria-label={label} className="scope-popover">
      {children}
    </div>
  );
}
