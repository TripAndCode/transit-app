import { useRef } from "react";
import { clampQueueWidth, DEFAULT_QUEUE_WIDTH, MAX_QUEUE_WIDTH, MIN_QUEUE_WIDTH } from "./queueWidth";

/** Drag handle between the map and the side panel. The panel is the right-hand
 *  column, so a drag toward the left (negative clientX delta) widens it.
 *
 *  A focusable `separator` carrying `aria-valuenow` is WAI-ARIA's window-splitter
 *  pattern, which jsx-a11y's non-interactive rules don't model; the keyboard
 *  handler below is what makes it operable without a pointer. */
export function QueueResizer({ width, label, onWidth, onCommit }: {
  width: number; label: string; onWidth: (px: number) => void; onCommit: (px: number) => void;
}) {
  const drag = useRef<{ x: number; width: number; last: number } | null>(null);
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- see the splitter note above
    <div
      className="ops-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={MIN_QUEUE_WIDTH}
      aria-valuemax={MAX_QUEUE_WIDTH}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- see the splitter note above
      tabIndex={0}
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, width, last: width };
        e.currentTarget.setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        const next = clampQueueWidth(drag.current.width - (e.clientX - drag.current.x));
        drag.current.last = next;
        onWidth(next);
      }}
      onPointerUp={(e) => {
        if (drag.current) onCommit(drag.current.last);
        drag.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      }}
      onDoubleClick={() => { onWidth(DEFAULT_QUEUE_WIDTH); onCommit(DEFAULT_QUEUE_WIDTH); }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 48 : 16;
        if (e.key === "ArrowLeft") { e.preventDefault(); const next = clampQueueWidth(width + step); onWidth(next); onCommit(next); }
        if (e.key === "ArrowRight") { e.preventDefault(); const next = clampQueueWidth(width - step); onWidth(next); onCommit(next); }
      }}
    />
  );
}
