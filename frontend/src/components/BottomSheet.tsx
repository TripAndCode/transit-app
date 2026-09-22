import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { GripHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { clampRatio, nextSnap, SNAP_HEIGHT_VH, SNAP_ORDER, SNAP_RATIO, type SnapPoint } from "./bottomSheetSnap";

export type { SnapPoint };

type Props = {
  snap: SnapPoint;
  onSnapChange: (next: SnapPoint) => void;
  ariaLabel: string;
  children: ReactNode;
};

/**
 * A three-snap-point sheet for phone-width chrome that has to host both a
 * quick glance (peek) and content that needs real room (full) without ever
 * being a full-screen modal by default. Only "full" behaves like one: at
 * that point the sheet's own height covers essentially the whole viewport
 * below the tab bar, so it traps Tab and marks itself `aria-modal` the same
 * way a dialog would (see `useFocusTrap`); at "peek"/"half" it is a normal
 * in-flow panel layered over the map, and focus is free to leave it.
 */
export function BottomSheet({ snap, onSnapChange, ariaLabel, children }: Props) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const dragStateRef = useRef<{
    startY: number;
    startTime: number;
    lastY: number;
    lastTime: number;
    /** The panel's height when the drag began. Measured once: `snap` cannot
     *  change mid-gesture, so re-reading it per move would only force a
     *  synchronous layout between the previous frame's write and the next. */
    restingHeight: number;
  } | null>(null);
  const [dragRatio, setDragRatio] = useState<number | null>(null);
  const isFull = snap === "full";

  useFocusTrap(isFull, panelRef, () => onSnapChange("half"));

  /** The pointer's height, expressed on the same peek-anchored 0..1 scale as
   *  `SNAP_RATIO` and `heightVh`. Normalizing against the full height alone
   *  would put peek at 0.159 rather than 0, so the first move of a drag that
   *  has not travelled yet would re-render the sheet at a different height
   *  than it is resting at. */
  function ratioFromClientY(clientY: number): number {
    const viewportH = window.innerHeight || 1;
    const panelH = dragStateRef.current?.restingHeight ?? viewportH * (SNAP_HEIGHT_VH[snap] / 100);
    const draggedHeight = panelH - (clientY - (dragStateRef.current?.startY ?? clientY));
    const peekH = viewportH * (SNAP_HEIGHT_VH.peek / 100);
    const fullH = viewportH * (SNAP_HEIGHT_VH.full / 100);
    return clampRatio((draggedHeight - peekH) / (fullH - peekH));
  }

  function onHandlePointerDown(e: ReactPointerEvent<HTMLButtonElement>) {
    e.currentTarget.setPointerCapture(e.pointerId);
    const now = performance.now();
    const viewportH = window.innerHeight || 1;
    dragStateRef.current = {
      startY: e.clientY,
      startTime: now,
      lastY: e.clientY,
      lastTime: now,
      // `||`, not `??`: an unlaid-out panel measures 0 rather than nothing,
      // and a 0 resting height would place the very first pointermove far
      // below peek and clamp the sheet shut.
      restingHeight:
        panelRef.current?.getBoundingClientRect().height || viewportH * (SNAP_HEIGHT_VH[snap] / 100),
    };
    setDragRatio(SNAP_RATIO[snap]);
  }

  function onHandlePointerMove(e: ReactPointerEvent<HTMLButtonElement>) {
    if (!dragStateRef.current) return;
    const now = performance.now();
    dragStateRef.current.lastY = e.clientY;
    dragStateRef.current.lastTime = now;
    setDragRatio(ratioFromClientY(e.clientY));
  }

  function onHandlePointerUp(e: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragStateRef.current;
    dragStateRef.current = null;
    if (!drag) return;
    const ratio = ratioFromClientY(e.clientY);
    const dt = Math.max(1, drag.lastTime - drag.startTime);
    // Velocity in the same 0..1 ratio units as position, per millisecond --
    // a positive dy (finger moved down) should read as a positive
    // (peek-ward) velocity, matching nextSnap's sign convention.
    const dyRatio = (drag.lastY - drag.startY) / (window.innerHeight || 1);
    const velocity = dyRatio / dt;
    setDragRatio(null);
    onSnapChange(nextSnap(ratio, velocity));
  }

  function onHandleKeyDown(e: ReactKeyboardEvent<HTMLButtonElement>) {
    const idx = SNAP_ORDER.indexOf(snap);
    if (e.key === "ArrowUp" && idx < SNAP_ORDER.length - 1) {
      e.preventDefault();
      onSnapChange(SNAP_ORDER[idx + 1]);
    } else if (e.key === "ArrowDown" && idx > 0) {
      e.preventDefault();
      onSnapChange(SNAP_ORDER[idx - 1]);
    }
  }

  const heightVh = dragRatio != null
    ? SNAP_HEIGHT_VH.peek + dragRatio * (SNAP_HEIGHT_VH.full - SNAP_HEIGHT_VH.peek)
    : SNAP_HEIGHT_VH[snap];

  return (
    <div
      ref={panelRef}
      role={isFull ? "dialog" : undefined}
      aria-modal={isFull ? true : undefined}
      aria-label={ariaLabel}
      tabIndex={-1}
      className="bottom-sheet"
      style={{
        height: `${heightVh}vh`,
        transition: dragRatio != null ? "none" : "height var(--dur-2) var(--ease-out)",
      }}
    >
      <button
        type="button"
        className="bottom-sheet__handle"
        aria-label={t("sheet.handle")}
        aria-expanded={snap !== "peek"}
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerUp}
        onPointerCancel={onHandlePointerUp}
        onKeyDown={onHandleKeyDown}
      >
        <GripHorizontal size={16} strokeWidth={1.5} aria-hidden="true" />
      </button>
      <div className="bottom-sheet__body">{children}</div>
    </div>
  );
}
