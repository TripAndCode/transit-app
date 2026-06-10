import type { TFunction } from "i18next";
import { useRouteTrips, useRouteStopProfile } from "../../api/hooks";
import { delayColor } from "../../styles/tokens";
import { Spinner } from "../../components/Spinner";

function signedMin(sec: number, t: TFunction): string {
  const m = Math.round(sec / 60);
  return t("common.unit_min_signed", { sign: sec < 0 ? "-" : "+", value: Math.abs(m) });
}

export function RouteDrilldown({
  agencyId,
  routeCode,
  routeName,
  onClose,
  t,
}: {
  agencyId: number;
  routeCode: string;
  routeName: string;
  onClose: () => void;
  t: TFunction;
}) {
  const trips = useRouteTrips(agencyId, routeCode);
  const stops = useRouteStopProfile(agencyId, routeCode);

  return (
    <aside
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(440px, 100%)",
        background: "var(--bg-surface)",
        borderLeft: "1px solid var(--border-soft)",
        boxShadow: "-8px 0 24px rgba(0,0,0,0.06)",
        padding: 20,
        overflowY: "auto",
        zIndex: 40,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 16, flex: 1 }}>{t("live.drill.title", { route: routeName })}</h3>
        <button type="button" onClick={onClose} style={{ background: "transparent", border: "1px solid var(--border-subtle)", borderRadius: 4, padding: "4px 12px" }}>
          {t("live.drill.close")}
        </button>
      </div>

      <h4 style={{ fontSize: 13, color: "var(--text-secondary)" }}>{t("live.drill.trips_heading")}</h4>
      {trips.isLoading && <Spinner />}
      {trips.data?.trips.length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("live.drill.empty")}</p>}
      {trips.data?.trips.map((tr) => (
        <div key={tr.trip_id} style={{ display: "flex", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--border-subtle)" }}>
          <span style={{ flex: 1 }}>
            {tr.headsign
              ? t("live.drill.trip_label", { time: tr.scheduled_time ?? "-", headsign: tr.headsign })
              : t("live.drill.trip_label_no_headsign", { time: tr.scheduled_time ?? "-" })}
          </span>
          <span aria-hidden="true" style={{ color: delayColor(tr.avg_delay_sec / 60), fontSize: 10 }}>&#9679;</span>
          <span style={{ fontWeight: 600 }}>{signedMin(tr.avg_delay_sec, t)}</span>
        </div>
      ))}

      <h4 style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 20 }}>{t("live.drill.stops_heading")}</h4>
      {stops.isLoading && <Spinner />}
      {stops.data?.stops.map((s) => (
        <div key={s.stop_sequence} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0" }}>
          <span style={{ flex: 1, fontSize: 13 }}>
            {s.stop_name
              ? t("live.drill.stop_label", { seq: s.stop_sequence, name: s.stop_name })
              : t("live.drill.stop_label_no_name", { seq: s.stop_sequence })}
          </span>
          <span
            aria-hidden="true"
            style={{
              height: 8,
              borderRadius: 2,
              background: delayColor(s.avg_delay_sec / 60),
              width: Math.max(4, Math.min(120, Math.round(s.avg_delay_sec / 60) * 12)),
            }}
          />
          <span style={{ fontSize: 12, fontWeight: 600, minWidth: 48, textAlign: "right" }}>{signedMin(s.avg_delay_sec, t)}</span>
        </div>
      ))}
    </aside>
  );
}
