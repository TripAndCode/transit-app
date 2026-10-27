import { useEffect, useId, useState, type CSSProperties } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { Check } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { Z_INDEX } from "../styles/zIndex";
import { useAgencies } from "../api/hooks";
import { useConfig } from "../api/config";
import { useSession } from "../api/auth";
import { readLastAgency, writeLastAgency } from "../api/lastAgency";
import { IndexLoadingPlaceholder } from "./RoutePlaceholders";
import { ErrorBanner } from "./ErrorBanner";
import type { Agency } from "../api/types";
import { formatDate } from "../utils/format";
import { LanguageToggle } from "./LanguageToggle";
import { searchKey } from "../utils/searchText";
import { groupAgencies, hasStopped, newestDataDate } from "./agencyDirectory";

// Selection→navigate delay: must stay >= --transition (global.css) so the
// dim/highlight finishes before the route changes. Not derived from the CSS
// var directly (no runtime cost of reading it) — bump both together if
// --transition ever grows past this.
const SELECT_TRANSITION_MS = 250;

// Past this many cards, finding one by eye is slower than typing its name.
const SEARCH_MIN_AGENCIES = 9;

/** A day without its year when it shares one with `reference`; non-breaking
 *  spaces hold "Oct 14" together, so a narrow card wraps before the date
 *  rather than inside it. */
function shortDay(iso: string, reference: string): string {
  return formatDate(iso, { year: iso.slice(0, 4) !== reference.slice(0, 4) }).replaceAll(" ", "\u00a0");
}

// Vertical padding gives a finger the height the small text alone lacks.
const FOOTER_LINK: CSSProperties = { display: "inline-block", padding: "6px 0", color: "var(--text-secondary)" };

/** Owns the "/" landing decision for a visitor RequireAuth has let through:
 *  while agencies load, show the existing placeholder;
 *  once loaded, instantly redirect (via the declarative <Navigate> element —
 *  this runs at render time, so calling useNavigate() imperatively here
 *  instead would violate render purity) for the single-agency or
 *  remembered-choice case; only render the agency picker page when there's a
 *  real choice to make. */
export function OnboardingGate() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: agencies, isLoading, isError, error, refetch } = useAgencies();
  const { data: config } = useConfig();
  const { data: session } = useSession();
  const headingId = useId();
  // Hooks must run unconditionally on every render (Rules of Hooks) — declared
  // here, above the early returns below, rather than next to select() where
  // it's used.
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Snapshotted once at mount (lazy initializer), not re-read on every render:
  // select() below writes a fresh choice to localStorage synchronously, and a
  // live read here would immediately match that write on the next render,
  // short-circuiting straight to <Navigate> and skipping the transition.
  const [remembered] = useState(() => readLastAgency());
  const [query, setQuery] = useState("");

  // Deferred navigate lives in an effect (not the click handler's own
  // setTimeout) so an unmount inside the delay window cleans up the timer
  // instead of leaving it to call navigate() on a gone component.
  useEffect(() => {
    if (selectedId == null) return;
    const id = setTimeout(() => navigate(`/agencies/${selectedId}/pulse`, { replace: true }), SELECT_TRANSITION_MS);
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
    return <Navigate to={`/agencies/${agencies[0].agency_id}/pulse`} replace />;
  }

  if (remembered != null && agencies.some((a) => a.agency_id === remembered)) {
    return <Navigate to={`/agencies/${remembered}/pulse`} replace />;
  }

  function select(agency: Agency) {
    setSelectedId(agency.agency_id);
    writeLastAgency(agency.agency_id);
  }

  const searchable = agencies.length >= SEARCH_MIN_AGENCIES;
  const needle = searchKey(query);
  const shown = agencies.filter((a) => !searchable || searchKey(a.agency_name).includes(needle));
  const newest = newestDataDate(agencies);
  const groups = groupAgencies(shown, newest);
  // A lone group of agencies with no known prefecture has nothing to be told apart from.
  const headed = groups.some((g) => g.prefectureKey != null);
  // Only a visitor known to be signed out, on a deployment that lets them in
  // that way, is told login is unneeded or offered one.
  const signedOut = session === null;
  const guestWelcome = signedOut && config != null && !config.login_required;
  const canLogIn = signedOut && config?.auth_enabled === true;

  // The page scrolls as a whole and its content starts at the top: a long
  // list on a short screen then runs down past the fold instead of being
  // centred with its head pushed out of scroll reach.
  return (
    <div
      data-testid="agency-directory"
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--bg-page)",
        overflowY: "auto",
        zIndex: Z_INDEX.modal,
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 760,
          margin: "0 auto",
          padding: "16px clamp(16px, 4vw, 32px) 40px",
          boxSizing: "border-box",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: 700,
              fontSize: "var(--text-sm)",
              color: "var(--text-secondary)",
            }}
          >
            {t("header.app_title")}
          </span>
          <LanguageToggle />
        </div>
        <h1
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 700,
            fontSize: "var(--text-xl)",
            margin: "clamp(24px, 5vw, 40px) 0 8px",
            letterSpacing: "0.01em",
            color: "var(--text-primary)",
          }}
        >
          {t("onboarding.title")}
        </h1>
        <p style={{ margin: 0, fontSize: "var(--text-base)", lineHeight: 1.6, color: "var(--text-secondary)" }}>
          {guestWelcome ? (
            // The second sentence wraps as a whole, never inside a word.
            <Trans t={t} i18nKey="onboarding.intro_guest" components={{ unit: <span style={{ display: "inline-block" }} /> }} />
          ) : (
            t("onboarding.intro")
          )}
        </p>
        {searchable && (
          <input
            type="search"
            aria-label={t("onboarding.search")}
            placeholder={t("onboarding.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: "100%", marginTop: 24 }}
          />
        )}
        {newest && (
          <p style={{ margin: "12px 0 0", fontSize: "var(--text-sm)", color: "var(--text-tertiary)" }}>
            {t("onboarding.data_through", { date: shortDay(newest, newest) })}
          </p>
        )}
        {shown.length === 0 && (
          <p role="status" style={{ margin: "24px 0 0", color: "var(--text-tertiary)", fontSize: "var(--text-sm)" }}>
            {t("onboarding.no_matches")}
          </p>
        )}
        {groups.map((group, i) => (
          <section
            key={group.prefectureKey ?? "other"}
            aria-labelledby={headed ? `${headingId}-${i}` : undefined}
            style={{ marginTop: headed ? 28 : 20 }}
          >
            {headed && (
              <h2
                id={`${headingId}-${i}`}
                style={{
                  margin: "0 0 10px",
                  fontSize: "var(--text-sm)",
                  fontWeight: 600,
                  color: "var(--text-secondary)",
                }}
              >
                {group.prefectureKey ? t(group.prefectureKey) : t("onboarding.other_prefecture")}{" "}
                <span style={{ fontWeight: 400, color: "var(--text-tertiary)" }}>
                  {t("onboarding.group_count", { count: group.agencies.length })}
                </span>
              </h2>
            )}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                gap: 10,
              }}
            >
              {group.agencies.map((a) => (
                <AgencyCard
                  key={a.agency_id}
                  agency={a}
                  newest={newest}
                  selectedId={selectedId}
                  onSelect={() => select(a)}
                />
              ))}
            </div>
          </section>
        ))}
        <nav
          aria-label={t("onboarding.links")}
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: "4px 10px",
            marginTop: 40,
            paddingTop: 16,
            borderTop: "1px solid var(--border-soft)",
            fontSize: "var(--text-sm)",
          }}
        >
          <Link to="/help" style={FOOTER_LINK}>
            {t("nav.help")}
          </Link>
          <span aria-hidden="true" style={{ color: "var(--text-tertiary)" }}>
            ·
          </span>
          <Link to="/welcome" style={FOOTER_LINK}>
            {t("nav.about")}
          </Link>
          {canLogIn && (
            <>
              <span aria-hidden="true" style={{ color: "var(--text-tertiary)" }}>
                ·
              </span>
              <Link to="/login" style={FOOTER_LINK}>
                {t("common.login")}
              </Link>
            </>
          )}
        </nav>
      </div>
    </div>
  );
}

function AgencyCard({
  agency,
  newest,
  selectedId,
  onSelect,
}: {
  agency: Agency;
  newest: string | null;
  selectedId: number | null;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const isSelected = selectedId === agency.agency_id;
  const isDimmed = selectedId != null && !isSelected;
  const latest = agency.latest_data_date;
  const stopped = latest != null && newest != null && hasStopped(latest, newest);
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={selectedId != null}
      style={{
        position: "relative",
        background: isSelected ? "var(--accent-soft)" : "var(--bg-surface)",
        border: `1px solid ${isSelected ? "var(--accent)" : "var(--border-subtle)"}`,
        borderRadius: "var(--radius)",
        padding: "14px 16px",
        textAlign: "left",
        cursor: selectedId != null ? "default" : "pointer",
        color: "var(--text-primary)",
        font: "inherit",
        opacity: isDimmed ? 0.25 : 1,
        transition: "background var(--transition), border-color var(--transition), opacity var(--transition)",
      }}
    >
      {isSelected && (
        <span
          data-testid="agency-check-badge"
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 10,
            right: 10,
            width: 20,
            height: 20,
            borderRadius: "50%",
            background: "var(--accent)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Check size={12} strokeWidth={3} color="var(--on-accent)" />
        </span>
      )}
      <div style={{ fontSize: "var(--text-base)", fontWeight: 600 }}>{agency.agency_name}</div>
      {stopped && (
        <div style={{ fontSize: "var(--text-xs)", marginTop: 4, color: "var(--color-warning-text)" }}>
          {t("onboarding.stopped", { date: shortDay(latest, newest) })}
        </div>
      )}
      {latest == null && (
        <div style={{ fontSize: "var(--text-xs)", marginTop: 4, color: "var(--text-tertiary)" }}>
          {t("onboarding.no_data_yet")}
        </div>
      )}
    </button>
  );
}
