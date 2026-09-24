import type { ReactNode } from "react";
import "./ui.css";

/** The top of a page: one `<h1>`, optional eyebrow and subtitle, and a slot
 *  for the page-level actions that belong beside the title rather than
 *  inside the content. */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
  className,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={["ui-page-header", className].filter(Boolean).join(" ")}>
      <div className="ui-page-header__text">
        {eyebrow != null && <span className="ui-page-header__eyebrow">{eyebrow}</span>}
        <h1 className="ui-page-header__title">{title}</h1>
        {subtitle != null && <p className="ui-page-header__subtitle">{subtitle}</p>}
      </div>
      {actions != null && <div className="ui-page-header__actions">{actions}</div>}
    </header>
  );
}
