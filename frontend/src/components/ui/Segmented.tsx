import type { CSSProperties, KeyboardEvent } from "react";
import { useRef, useState } from "react";
import "./ui.css";

export type SegmentedOption<T extends string> = {
  value: T;
  label: string;
  /** A figure under the label, such as the share a choice would give.
   *  Kept out of the tooltip, which names only the choice. */
  meta?: string;
};

type SegmentedProps<T extends string> = {
  options: SegmentedOption<T>[];
  /** The option currently selected; `null` when nothing matches (a custom
   *  range, a hand-set hour), which hides the thumb instead of guessing a
   *  nearest cell. */
  value: T | null;
  onChange: (value: T) => void;
  "aria-label": string;
  /** Wraps the options onto more than one row; defaults to a single row. */
  columns?: number;
};

/** The track's padding, which the thumb's inset and size are measured from. */
const INSET_PX = 3;

/** A single-choice control: a soft track whose selected thumb slides to the
 *  chosen cell. Implements the WAI-ARIA radiogroup pattern (roving tabindex,
 *  arrow/Home/End navigation) over plain buttons, since a native
 *  `<input type="radio">` cannot host the sliding thumb. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  "aria-label": ariaLabel,
  columns,
}: SegmentedProps<T>) {
  const cols = columns ?? options.length;
  const hasMeta = options.some((o) => o.meta);
  const rows = Math.ceil(options.length / cols);
  const selectedIndex = options.findIndex((o) => o.value === value);
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // The selection is the roving tab stop; with none (a custom range/hour),
  // the first option is, as the WAI-ARIA radiogroup pattern specifies.
  const tabbableIndex = Math.max(selectedIndex, 0);
  // While nothing matches, the hidden thumb stays where it last stood, and it
  // fades in at the next choice instead of sliding there: a slide from the
  // hidden spot would sweep past options the user never picked.
  const [thumb, setThumb] = useState({ selected: selectedIndex, at: Math.max(selectedIndex, 0), appearing: false });
  if (thumb.selected !== selectedIndex) {
    setThumb({
      selected: selectedIndex,
      at: selectedIndex === -1 ? thumb.at : selectedIndex,
      appearing: thumb.selected === -1,
    });
  }

  function choose(index: number, moveFocus: boolean) {
    if (moveFocus) buttonRefs.current[index]?.focus();
    onChange(options[index].value);
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const count = options.length;
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (index + 1) % count;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (index - 1 + count) % count;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = count - 1;
    if (next === null) return;
    e.preventDefault();
    choose(next, true);
  }

  const thumbStyle: CSSProperties = {
    top: INSET_PX,
    left: INSET_PX,
    width: `calc((100% - ${2 * INSET_PX}px) / ${cols})`,
    height: `calc((100% - ${2 * INSET_PX}px) / ${rows})`,
    // A percentage translate is of the thumb's own size, i.e. one cell.
    transform: `translate(${(thumb.at % cols) * 100}%, ${Math.floor(thumb.at / cols) * 100}%)`,
    opacity: selectedIndex === -1 ? 0 : 1,
  };

  return (
    <div className={hasMeta ? "seg seg--meta" : "seg"} role="radiogroup" aria-label={ariaLabel} style={{ padding: INSET_PX, gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      <span aria-hidden="true" className={thumb.appearing ? "seg-thumb seg-thumb--appear" : "seg-thumb"} style={thumbStyle} />
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(el) => {
            buttonRefs.current[index] = el;
          }}
          type="button"
          role="radio"
          aria-checked={index === selectedIndex}
          tabIndex={index === tabbableIndex ? 0 : -1}
          className="seg-btn"
          title={option.label}
          onClick={() => choose(index, false)}
          onKeyDown={(e) => onKeyDown(e, index)}
        >
          <span className="seg-btn__label">{option.label}</span>
          {/* The space keeps the label and the figure apart in the
              button's accessible name; a grid drops it from the layout. */}
          {option.meta && (
            <>
              {" "}
              <span className="seg-btn__meta">{option.meta}</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}
