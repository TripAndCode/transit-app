import type { ReactElement } from "react";
import { Tooltip } from "./Tooltip";

/** Rail links only carry a tooltip while the rail is collapsed -- expanded,
 *  the label is already on screen and a bubble repeating it is noise. The
 *  same collapse also strips the visible text, so the link takes an
 *  `aria-label` there: the tooltip describes a control, it never names one. */
export function RailTooltip({
  collapsed,
  label,
  children,
}: {
  collapsed: boolean;
  label: string;
  children: ReactElement;
}) {
  if (!collapsed) return children;
  return (
    <Tooltip label={label} placement="right">
      {children}
    </Tooltip>
  );
}
