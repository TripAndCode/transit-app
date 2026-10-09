/**
 * Spinner — minimal inline loading indicator for buttons and small surfaces.
 *
 * Renders a single SVG arc that rotates. Calm by default: muted accent color,
 * inherits font size, gentle rotation. Use inline in busy buttons or near
 * inline text where a Skeleton placeholder would be too much.
 *
 * Spinning is a stylesheet concern: `.ui-spinner svg` rotates inside the
 * motion-allowed block of `ui.css`, so a reduced-motion viewer sees the
 * static arc — the same rule every other loop in the app follows.
 */
import type { CSSProperties } from "react";
import "./ui/ui.css";

type SpinnerProps = {
  /** Pixel size; defaults to 14 (matches a 13px button label height). */
  size?: number;
  /** Stroke color; defaults to `currentColor` so it inherits button text color. */
  color?: string;
  /** Render inline-flex with a small right margin (useful as a button-text prefix). */
  inline?: boolean;
  /** Accessible label. Set when the spinner is not paired with adjacent text. */
  label?: string;
  style?: CSSProperties;
};

export function Spinner({
  size = 14,
  color = "currentColor",
  inline = false,
  label,
  style,
}: SpinnerProps) {
  const stroke = Math.max(1.4, size / 8);
  return (
    <span
      data-spinner
      className="ui-spinner"
      role={label ? "status" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      style={{
        display: inline ? "inline-flex" : undefined,
        alignItems: inline ? "center" : undefined,
        verticalAlign: inline ? "middle" : "baseline",
        marginRight: inline ? 6 : 0,
        width: size,
        height: size,
        ...style,
      }}
    >
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
        <circle
          cx="12"
          cy="12"
          r="9"
          stroke={color}
          strokeOpacity="0.22"
          strokeWidth={stroke}
          fill="none"
        />
        <path
          d="M21 12a9 9 0 0 1-9 9"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          fill="none"
        />
      </svg>
    </span>
  );
}
