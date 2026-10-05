import type { ReactNode } from "react";
import { Toolbar } from "./Toolbar";
import "./ui.css";

/** A titled block within a page: eyebrow, `<h2>`, optional one-line
 *  description, an actions slot on the title row, then the content.
 *
 *  `open` opts into the collapsible form, which is the same section drawn
 *  inside a `<details>`. It is controlled rather than left to the browser so
 *  the open state can live wherever the page keeps the rest of its view
 *  state; a section whose openness is worth sharing keeps it in the URL. An
 *  actions row is dropped in that form: a control inside `<summary>` is
 *  swallowed by the disclosure's own activation behaviour. */
export function Section({
  eyebrow,
  title,
  description,
  actions,
  className,
  open,
  onOpenChange,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: ReactNode;
}) {
  const classes = ["ui-section", className].filter(Boolean).join(" ");
  const body = (
    <>
      {description != null && <p className="ui-section__description">{description}</p>}
      <div className="ui-section__body">{children}</div>
    </>
  );

  if (open !== undefined) {
    return (
      <details className={`${classes} ui-section--collapsible`} open={open}>
        {/* The eyebrow and heading sit directly in the summary rather than
            in the flex `__head` wrapper the open form uses: `display: flex`
            on a summary drops the native disclosure marker, and phrasing
            plus heading content is exactly what summary's content model
            allows. */}
        {/* The disclosure is driven from `open`, not from the browser's own
            toggling of the attribute: default-prevented here so the DOM
            never disagrees with the state the caller holds. Enter and Space
            on a summary dispatch a click too, so the keyboard path is the
            same one. */}
        <summary
          className="ui-section__summary"
          onClick={(e) => {
            e.preventDefault();
            onOpenChange?.(!open);
          }}
        >
          {eyebrow != null && <span className="ui-section__eyebrow">{eyebrow}</span>}
          <h2 className="ui-section__title">{title}</h2>
        </summary>
        {body}
      </details>
    );
  }

  return (
    <section className={classes}>
      <div className="ui-section__head">
        <div className="ui-section__text">
          {eyebrow != null && <span className="ui-section__eyebrow">{eyebrow}</span>}
          <h2 className="ui-section__title">{title}</h2>
        </div>
        {actions != null && <Toolbar align="end">{actions}</Toolbar>}
      </div>
      {body}
    </section>
  );
}
