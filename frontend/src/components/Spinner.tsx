/**
 * Spinner — minimal inline loading indicator for buttons and small surfaces.
 *
 * Renders a single SVG arc that rotates. Calm by default: muted accent color,
 * inherits font size, gentle rotation. Use inline in busy buttons or near
 * inline text where a Skeleton placeholder would be too much.
 *
 * Respects `prefers-reduced-motion` — when the user has motion reduced, the
 * arc shows static and a dotted ring fades softly instead of spinning.
 */
import type { CSSProperties } from "react";

export type SpinnerProps = {
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
      role={label ? "status" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      style={{
        display: inline ? "inline-flex" : "inline-block",
        alignItems: inline ? "center" : undefined,
        verticalAlign: inline ? "middle" : "baseline",
        marginRight: inline ? 6 : 0,
        width: size,
        height: size,
        ...style,
      }}
    >
      <svg
        viewBox="0 0 24 24"
        width={size}
        height={size}
        fill="none"
        style={{
          animation: "spinner-rotate 0.9s linear infinite",
          transformBox: "fill-box",
          transformOrigin: "50% 50%",
        }}
      >
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
      <style>{`
        @keyframes spinner-rotate {
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-spinner] svg {
            animation-duration: 2.4s !important;
          }
        }
      `}</style>
    </span>
  );
}
