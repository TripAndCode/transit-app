import type { ReactNode } from "react";
import "./ui.css";

/** A wrapping row of controls. `label` opts into the ARIA toolbar role --
 *  only correct when the row really is a grouped set of controls; a row that
 *  merely happens to hold one button stays a plain div. */
export function Toolbar({
  align = "start",
  label,
  className,
  children,
}: {
  align?: "start" | "end";
  label?: string;
  className?: string;
  children?: ReactNode;
}) {
  const classes = ["ui-toolbar", align === "end" ? "ui-toolbar--end" : null, className]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={classes} role={label ? "toolbar" : undefined} aria-label={label}>
      {children}
    </div>
  );
}
