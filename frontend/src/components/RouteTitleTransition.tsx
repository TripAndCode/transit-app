import { ViewTransition, type ReactNode } from "react";

/** The one shared element in the app: a route's label travelling from its
 *  report row into the dossier's title during a screen navigation. A view
 *  transition name must be unique on the page, so a row wears it only once
 *  clicked and the title wears it while a route is on the page. It animates
 *  only as a pair; entering or leaving alone it is part of the routed pane. */
export function RouteTitleTransition({ children }: { children: ReactNode }) {
  return (
    <ViewTransition name="route-title" share="route-title" default="none">
      {children}
    </ViewTransition>
  );
}
