import type { CSSProperties, KeyboardEventHandler, MouseEventHandler, ReactNode } from "react";
import "./ui.css";

/** A resting surface: the one place the card border, radius and elevation
 *  are decided. `as` exists for the surfaces that are also a landmark (the
 *  login card is the page's `<main>`); it never changes the painting.
 *  The interaction/testing props below exist only for a card that is itself
 *  the clickable element: pair `onClick` with `role="button"`, `tabIndex={0}`
 *  and `onKeyDown` (see `utils/a11y.ts`'s `onActivateKey`) so it stays
 *  keyboard-operable, and add `className="ui-card--clickable"` for the
 *  matching hover/focus affordance. */
export function Card({
  as: Tag = "div",
  padded = true,
  className,
  style,
  children,
  onClick,
  onKeyDown,
  role,
  tabIndex,
  "aria-label": ariaLabel,
  "data-testid": dataTestId,
}: {
  as?: "div" | "section" | "article" | "main" | "li";
  padded?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
  onClick?: MouseEventHandler<HTMLElement>;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  role?: string;
  tabIndex?: number;
  "aria-label"?: string;
  "data-testid"?: string;
}) {
  const classes = ["ui-card", padded ? "ui-card--padded" : null, className].filter(Boolean).join(" ");
  return (
    <Tag
      className={classes}
      style={style}
      onClick={onClick}
      onKeyDown={onKeyDown}
      role={role}
      tabIndex={tabIndex}
      aria-label={ariaLabel}
      data-testid={dataTestId}
    >
      {children}
    </Tag>
  );
}
