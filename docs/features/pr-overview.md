The overview now opens the realtime map. Route and service-pattern filters
scope markers, the delay queue and CSV to the same observations. Reports older
than ten minutes expire from the current view even without a successful refetch.

Removed the large KPI strip and history-mode switch. Trip progress and direction
selection remain available under observed-trip details. The old historical
overview remains at `/period-overview`. The next PR supplies the dedicated
route-analysis destination (currently routed to the existing analysis screen).

Validation: frontend typecheck and focused ESLint passed. No CI wait requested.
Draft stack: depends on #437; do not merge independently of design review.
