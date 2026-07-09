import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";
import { readLastAgency, writeLastAgency } from "../api/lastAgency";
import { IndexLoadingPlaceholder } from "./RoutePlaceholders";
import { ErrorBanner } from "./ErrorBanner";
import type { Agency } from "../api/types";

// Selection→navigate delay: must stay >= --transition (global.css) so the
// dim/highlight finishes before the route changes. Not derived from the CSS
// var directly (no runtime cost of reading it) — bump both together if
// --transition ever grows past this.
const SELECT_TRANSITION_MS = 250;

/** Owns the "/" landing decision: while agencies load, show the existing
 *  placeholder; once loaded, instantly redirect (via the declarative
 *  <Navigate> element — this runs at render time, so calling useNavigate()
 *  imperatively here instead would violate render purity) for the
 *  single-agency or remembered-choice case, identical to the old silent
 *  auto-redirect; only render the picker overlay when there's a real choice
 *  to make. */
export function OnboardingGate() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: agencies, isLoading, isError, error, refetch } = useAgencies();
  // Hooks must run unconditionally on every render (Rules of Hooks) — declared
  // here, above the early returns below, rather than next to select() where
  // it's used.
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Snapshotted once at mount (lazy initializer), not re-read on every render:
  // select() below writes a fresh choice to localStorage synchronously, and a
  // live read here would immediately match that write on the next render,
  // short-circuiting straight to <Navigate> and skipping the transition.
  const [remembered] = useState(() => readLastAgency());

  // Deferred navigate lives in an effect (not the click handler's own
  // setTimeout) so an unmount inside the delay window cleans up the timer
  // instead of leaving it to call navigate() on a gone component.
  useEffect(() => {
    if (selectedId == null) return;
    const id = setTimeout(() => navigate(`/agencies/${selectedId}/map`, { replace: true }), SELECT_TRANSITION_MS);
    return () => clearTimeout(id);
  }, [selectedId, navigate]);

  if (isLoading) return <IndexLoadingPlaceholder />;
  // Only surface the error banner when there's no usable fallback: react-query
  // keeps the last successful `data` during a failed background refetch, so a
  // transient refetch failure shouldn't hide an already-loaded agency list.
  if (isError && !agencies) {
    return (
      <div style={{ padding: 24 }}>
        <ErrorBanner error={error} onRetry={() => refetch()} />
      </div>
    );
  }
  if (!agencies) return <IndexLoadingPlaceholder />;
  if (agencies.length === 0) {
    return <div style={{ padding: 24, color: "var(--text-tertiary)" }}>{t("onboarding.no_agencies")}</div>;
  }

  if (agencies.length === 1) {
    return <Navigate to={`/agencies/${agencies[0].agency_id}/map`} replace />;
  }

  if (remembered != null && agencies.some((a) => a.agency_id === remembered)) {
    return <Navigate to={`/agencies/${remembered}/map`} replace />;
  }

  function select(agency: Agency) {
    setSelectedId(agency.agency_id);
    writeLastAgency(agency.agency_id);
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--bg-page)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
        zIndex: 100,
      }}
    >
      <div style={{ width: "100%", maxWidth: 640, textAlign: "center" }}>
        <div style={{ marginBottom: 36 }}>
          <h1
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: 22,
              margin: 0,
              letterSpacing: "0.01em",
              color: "var(--text-primary)",
            }}
          >
            {t("header.app_title")}
          </h1>
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginTop: 6 }}>
            {t("onboarding.subtitle")}
          </div>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 12,
          }}
        >
          {agencies.map((a) => {
            const isSelected = selectedId === a.agency_id;
            const isDimmed = selectedId != null && !isSelected;
            return (
              <button
                key={a.agency_id}
                type="button"
                onClick={() => select(a)}
                disabled={selectedId != null}
                style={{
                  background: isSelected ? "var(--accent-soft)" : "var(--bg-surface)",
                  border: `1px solid ${isSelected ? "var(--accent)" : "var(--border-subtle)"}`,
                  borderRadius: "var(--radius)",
                  padding: "20px 18px",
                  textAlign: "left",
                  cursor: selectedId != null ? "default" : "pointer",
                  color: "var(--text-primary)",
                  font: "inherit",
                  opacity: isDimmed ? 0.25 : 1,
                  transition: "background var(--transition), border-color var(--transition), opacity var(--transition)",
                }}
              >
                <div style={{ fontSize: 15, fontWeight: 600 }}>{a.agency_name}</div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
