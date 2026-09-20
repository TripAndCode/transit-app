import type { ReactNode } from "react";
import "./ui.css";

/** A titled block within a page: eyebrow, `<h2>`, optional one-line
 *  description, an actions slot on the title row, then the content. */
export function Section({
  eyebrow,
  title,
  description,
  actions,
  className,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section className={["ui-section", className].filter(Boolean).join(" ")}>
      <div className="ui-section__head">
        <div className="ui-section__text">
          {eyebrow != null && <span className="ui-section__eyebrow">{eyebrow}</span>}
          <h2 className="ui-section__title">{title}</h2>
        </div>
        {actions != null && <div className="ui-toolbar ui-toolbar--end">{actions}</div>}
      </div>
      {description != null && <p className="ui-section__description">{description}</p>}
      <div className="ui-section__body">{children}</div>
    </section>
  );
}
