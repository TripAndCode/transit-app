import { addTransitionType, use, useState, useTransition, type MouseEvent, type ReactNode } from "react";
import { NavLink, useNavigate, type NavLinkProps } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Spinner } from "./Spinner";
import { NavPendingContext } from "./navPendingContext";
import "./navPending.css";

/** Shows that a navigation is under way. The app keeps the outgoing screen
 *  painted while the next one's chunk loads, which reads as a click that did
 *  nothing; a navigation started here runs as a transition whose pending
 *  state draws a progress bar across the top until the new screen commits. */
export function NavPendingProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [isPending, startTransition] = useTransition();
  const [target, setTarget] = useState<string | null>(null);

  function go(to: string) {
    setTarget(to);
    startTransition(() => {
      addTransitionType("nav");
      return navigate(to);
    });
  }

  return (
    <NavPendingContext value={{ pendingTo: isPending ? target : null, go }}>
      {isPending && <div className="nav-progress" role="progressbar" aria-label={t("common.loading")} />}
      {/* Always mounted: a live region announces changes to its text, not its
          own arrival, so it has to exist before the navigation starts. */}
      <p className="nav-progress-status" role="status">
        {isPending ? t("common.loading") : ""}
      </p>
      {children}
    </NavPendingContext>
  );
}

function isPlainLeftClick(e: MouseEvent<HTMLAnchorElement>): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

/** A NavLink that navigates through the nearest NavPendingProvider, marking
 *  itself busy until its screen arrives; `spinner={false}` leaves the mark
 *  to the progress bar where the link's layout has no room for one. A
 *  modified click (new tab, new window) stays the browser's. */
export function PendingNavLink({
  to,
  onClick,
  children,
  spinner = true,
  ...rest
}: Omit<NavLinkProps, "to" | "children"> & { to: string; children: ReactNode; spinner?: boolean }) {
  const nav = use(NavPendingContext);
  const pending = nav?.pendingTo === to;
  return (
    <NavLink
      {...rest}
      to={to}
      aria-busy={pending || undefined}
      onClick={(e) => {
        onClick?.(e);
        if (!nav || e.defaultPrevented || !isPlainLeftClick(e)) return;
        e.preventDefault();
        nav.go(to);
      }}
    >
      {children}
      {pending && spinner && <Spinner size={12} style={{ marginLeft: "auto" }} />}
    </NavLink>
  );
}
