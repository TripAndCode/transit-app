import type { CSSProperties, ReactNode } from "react";
import "./ui.css";

/** A resting surface: the one place the card border, radius and elevation
 *  are decided. `as` exists for the surfaces that are also a landmark (the
 *  login card is the page's `<main>`); it never changes the painting. */
export function Card({
  as: Tag = "div",
  padded = true,
  className,
  style,
  children,
}: {
  as?: "div" | "section" | "article" | "main" | "li";
  padded?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}) {
  const classes = ["ui-card", padded ? "ui-card--padded" : null, className].filter(Boolean).join(" ");
  return (
    <Tag className={classes} style={style}>
      {children}
    </Tag>
  );
}
