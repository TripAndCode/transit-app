import type { TFunction } from "i18next";
import { AlertTriangle, ChevronRight, MapPin, Route as RouteIcon } from "lucide-react";
import type { LiveTrip, RouteBucket, RouteSummary } from "../../api/types";
import { signedMin } from "../live/signedMin";

type Props = {
  routes: RouteSummary[];
  trips: LiveTrip[];
  selectedRoute: string | null;
  formatRoute: (routeCode: string) => string;
  onSelectRoute: (routeCode: string) => void;
  onOpenRoute: (route: RouteSummary) => void;
  t: TFunction;
};

function worstTripForRoute(trips: LiveTrip[], routeCode: string): LiveTrip | undefined {
  return trips
    .filter((trip) => trip.route_code === routeCode)
    .sort((a, b) => b.dep_delay - a.dep_delay)[0];
}

function bucketRoutes(routes: RouteSummary[], bucket: RouteBucket): RouteSummary[] {
  return routes
    .filter((route) => route.bucket === bucket)
    .sort((a, b) => (b.deviation_sec ?? b.avg_delay_sec) - (a.deviation_sec ?? a.avg_delay_sec));
}

export function OperationsQueue({ routes, trips, selectedRoute, formatRoute, onSelectRoute, onOpenRoute, t }: Props) {
  const anomaly = bucketRoutes(routes, "anomaly");
  const watch = bucketRoutes(routes, "watch");

  return (
    <aside className="ops-queue" aria-label={t("operations.queue.title")}>
      <div className="ops-queue__header">
        <div>
          <span className="ops-eyebrow">{t("operations.queue.eyebrow")}</span>
          <h2>{t("operations.queue.title")}</h2>
        </div>
        <span className="ops-queue__total">{t("operations.queue.total", { count: anomaly.length + watch.length })}</span>
      </div>

      <QueueSection
        bucket="anomaly"
        routes={anomaly}
        trips={trips}
        selectedRoute={selectedRoute}
        formatRoute={formatRoute}
        onSelectRoute={onSelectRoute}
        onOpenRoute={onOpenRoute}
        t={t}
      />
      <QueueSection
        bucket="watch"
        routes={watch}
        trips={trips}
        selectedRoute={selectedRoute}
        formatRoute={formatRoute}
        onSelectRoute={onSelectRoute}
        onOpenRoute={onOpenRoute}
        t={t}
      />

      {anomaly.length + watch.length === 0 && (
        <div className="ops-queue__clear">
          <span className="ops-queue__clear-mark">✓</span>
          <strong>{t("operations.queue.clear_title")}</strong>
          <span>{t("operations.queue.clear_body")}</span>
        </div>
      )}
      <div className="ops-queue__footer">
        {t("operations.queue.normal_routes", {
          // "no_baseline" routes are operating fine (below the raw-delay
          // thresholds) but have no historical baseline to deviate from --
          // they belong in the same "nothing to worry about" tally as normal.
          count: bucketRoutes(routes, "normal").length + bucketRoutes(routes, "no_baseline").length,
        })}
      </div>
    </aside>
  );
}

function QueueSection({
  bucket,
  routes,
  trips,
  selectedRoute,
  formatRoute,
  onSelectRoute,
  onOpenRoute,
  t,
}: Omit<Props, "routes"> & { bucket: "anomaly" | "watch"; routes: RouteSummary[] }) {
  if (routes.length === 0) return null;
  return (
    <section className={`ops-queue-section ops-queue-section--${bucket}`}>
      <h3>
        <AlertTriangle size={16} aria-hidden="true" />
        {t(`live.bucket.${bucket}`)} <span>{routes.length}</span>
      </h3>
      <div className="ops-queue-section__items">
        {routes.map((route) => {
          const trip = worstTripForRoute(trips, route.route_code);
          const deviation = route.deviation_sec;
          return (
            <article
              key={`${route.route_code}|${route.service_type}`}
              className={`ops-route-card ${selectedRoute === route.route_code ? "is-selected" : ""}`}
            >
              <button className="ops-route-card__main" type="button" onClick={() => onSelectRoute(route.route_code)}>
                <span className="ops-route-card__badge">{route.route_code.slice(0, 4)}</span>
                <span className="ops-route-card__copy">
                  <strong>{formatRoute(route.route_code)}</strong>
                  <b>
                    {deviation == null
                      ? t("operations.queue.current_delay", { delay: signedMin(route.avg_delay_sec, t) })
                      : t("operations.queue.deviation", { delay: signedMin(deviation, t) })}
                  </b>
                  <span>
                    <MapPin size={13} aria-hidden="true" />
                    {trip?.stop_name
                      ? t("operations.queue.reported_stop", { stop: trip.stop_name })
                      : t("operations.queue.location_unavailable")}
                  </span>
                </span>
                <ChevronRight size={18} aria-hidden="true" />
              </button>
              <div className="ops-route-card__actions">
                <button type="button" onClick={() => onSelectRoute(route.route_code)}>
                  <MapPin size={14} aria-hidden="true" /> {t("operations.queue.show_on_map")}
                </button>
                <button type="button" onClick={() => onOpenRoute(route)}>
                  <RouteIcon size={14} aria-hidden="true" /> {t("operations.queue.open_trip")}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
