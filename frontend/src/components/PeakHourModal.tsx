// frontend/src/components/PeakHourModal.tsx
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { PeakHourBreakdown } from "../api/types";
import { Spinner } from "./Spinner";

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

export function PeakHourModal({
  data,
  loading,
  onClose,
}: {
  data: PeakHourBreakdown | null;
  loading: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const maxAvg =
    data?.routes.length
      ? Math.max(...data.routes.map((r) => r.avg_min))
      : 1;

  const title =
    data == null
      ? ""
      : data.dow != null
        ? t("peakHourModal.title_dow", { dow: t(`forecast.dow_${WEEK[data.dow - 1]}`), hour: data.hour })
        : t("peakHourModal.title_all", { hour: data.hour });

  return (
    <>
      <div
        data-testid="peak-hour-modal-backdrop"
        onClick={onClose}
        aria-hidden="true"
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.35)",
          zIndex: 80,
        }}
      />
      <div
        role="dialog"
        aria-label={title}
        style={{
          position: "fixed",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(480px, 92vw)",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-soft)",
          borderRadius: 10,
          padding: 24,
          zIndex: 81,
          maxHeight: "80vh",
          overflowY: "auto",
        }}
      >
        <div
          style={{ display: "flex", alignItems: "center", marginBottom: 16 }}
        >
          <h3 style={{ flex: 1, margin: 0, fontSize: 15, fontWeight: 700 }}>
            {title}
          </h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "transparent",
              color: "var(--text-primary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 4,
              padding: "3px 10px",
              fontSize: 12,
            }}
          >
            {t("common.close")}
          </button>
        </div>
        {loading && <Spinner label={t("common.loading")} size={20} />}
        {!loading && data?.routes.length === 0 && (
          <p
            data-testid="peak-hour-modal-empty"
            style={{ color: "var(--text-tertiary)", fontSize: 13 }}
          >
            {t("peakHourModal.noData")}
          </p>
        )}
        {!loading &&
          data?.routes.map((r) => (
            <div
              key={`${r.route_code}-${r.service_type}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "7px 0",
                borderBottom: "1px solid var(--border-subtle)",
              }}
            >
              <span
                style={{ fontWeight: 600, minWidth: 48, fontSize: 13 }}
              >
                {r.route_code}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-tertiary)",
                  minWidth: 40,
                }}
              >
                {r.service_type}
              </span>
              <div
                style={{
                  flex: 1,
                  height: 8,
                  background: "var(--bg-soft)",
                  borderRadius: 4,
                  overflow: "hidden",
                }}
              >
                <div
                  data-testid={`peak-hour-modal-bar-${r.route_code}`}
                  style={{
                    height: "100%",
                    // avg_min can be negative for early-running routes; clamp
                    // to 0 (no bar) rather than emit an invalid negative CSS
                    // width, matching PeakHourRibbon's bar_h clamp. The text
                    // label still shows the true (possibly negative) value.
                    width: `${Math.max((r.avg_min / maxAvg) * 100, 0)}%`,
                    background: "var(--accent)",
                    borderRadius: 4,
                  }}
                />
              </div>
              <span
                style={{
                  minWidth: 52,
                  textAlign: "right",
                  fontWeight: 700,
                  fontSize: 13,
                }}
              >
                {t("peakHourModal.avgMin", { value: r.avg_min.toFixed(1) })}
              </span>
            </div>
          ))}
        {data?.routes.length ? (
          <p
            style={{
              fontSize: 11,
              color: "var(--text-tertiary)",
              marginTop: 10,
            }}
          >
            {t("peakHourModal.routeCount", { count: data.routes.length })}
          </p>
        ) : null}
      </div>
    </>
  );
}
