import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../api/auth";
import { apiDelete, apiGet } from "../api/client";
import { formatDateTime } from "../utils/format";
import { describeUserAgent } from "../utils/userAgent";
import { useAgencies } from "../api/hooks";
import { readLastAgency } from "../api/lastAgency";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Section } from "../components/ui/Section";
import { LegalLinks } from "../components/LegalLinks";
import { Skeleton } from "../components/Skeleton";

type SessionRow = {
  sid_prefix: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  /** The session this page was loaded with. */
  current: boolean;
};

/** The account page's outline while the session loads: a title, the
 *  profile card and two sections, so the page settles in place. */
function AccountSkeleton() {
  return (
    <div aria-hidden="true" style={{ maxWidth: 640, margin: "32px auto", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
      <Skeleton width="40%" height={28} />
      <Skeleton height={72} />
      <Skeleton width="30%" height={18} />
      <Skeleton height={48} />
      <Skeleton width="30%" height={18} />
      <Skeleton height={96} />
    </div>
  );
}

/** Self-service profile + active sessions + logout. */
export function AccountPage() {
  const { t } = useTranslation();
  const { data: session, isLoading } = useSession();
  const { data: sessions, isError: sessionsError } = useQuery({
    queryKey: ["mySessions"],
    queryFn: ({ signal }) => apiGet<SessionRow[]>("/api/me/sessions", { signal }),
    // A guest is on the way to sign-in; asking would only earn a 401.
    enabled: session != null,
  });
  const logout = useLogout();
  const qc = useQueryClient();
  const signOutSession = useMutation({
    mutationFn: (prefix: string) => apiDelete(`/api/me/sessions/${prefix}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ["mySessions"] }),
  });
  const lastAgencyId = readLastAgency();
  const lastAgency = useAgencies().data?.find((a) => a.agency_id === lastAgencyId);

  if (isLoading) return <AccountSkeleton />;
  if (!session) return <Navigate to={`/login?next=${encodeURIComponent("/me")}`} replace />;

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      {lastAgency && (
        <Link to={`/agencies/${lastAgency.agency_id}/pulse`} style={{ display: "inline-block", marginBottom: 12, fontSize: "var(--text-sm)" }}>
          {t("account.back_to_agency", { agency: lastAgency.agency_name })}
        </Link>
      )}
      <PageHeader title={t("account.title")} subtitle={session.email} />
      <Card style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--text-tertiary)" }}>{session.name ?? ""}</div>
        <div style={{ color: "var(--text-tertiary)", fontSize: "var(--text-sm)", marginTop: 4 }}>
          {t("account.role_label")}: {session.role === "admin" ? t("account.role.admin") : t("account.role.user")}
        </div>
      </Card>
      <Section title={t("account.linked_providers")}>
        {session.identities.length > 0 ? (
          <ul>{session.identities.map((i) => <li key={i.provider}>{i.provider}</li>)}</ul>
        ) : (
          <p style={{ margin: 0, color: "var(--text-tertiary)", fontSize: "var(--text-sm)" }}>{t("account.linked_none")}</p>
        )}
      </Section>
      <Section title={t("account.active_sessions")}>
        {sessions?.map((s) => {
          const device = describeUserAgent(s.user_agent);
          return (
            <Card key={s.sid_prefix} style={{ marginBottom: 6, fontSize: "var(--text-sm)", display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div>
                  {device ? t("account.session_device", device) : t("account.session_unknown_device")}
                  {s.current && <span className="caveat-badge" style={{ marginLeft: 8 }}>{t("account.this_device")}</span>}
                </div>
                <div style={{ color: "var(--text-tertiary)" }}>
                  {t("account.session_last_seen", { when: formatDateTime(s.last_seen_at) })}
                </div>
              </div>
              {/* This device signs out with the page's own button below. */}
              {!s.current && (
                <button
                  type="button"
                  aria-label={t("account.session_sign_out_aria")}
                  onClick={() => signOutSession.mutate(s.sid_prefix)}
                  disabled={signOutSession.isPending}
                >
                  {t("account.session_sign_out")}
                </button>
              )}
            </Card>
          );
        })}
        {sessionsError && (
          <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
            {t("account.sessions_load_error")}
          </p>
        )}
        {signOutSession.isError && (
          <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
            {t("account.session_sign_out_error")}
          </p>
        )}
      </Section>
      <button
        onClick={() => logout.mutate(undefined, { onSuccess: () => window.location.assign("/welcome") })}
        disabled={logout.isPending}
        style={{ padding: "8px 16px", background: "var(--surface-2)", color: "var(--text-primary)", border: "none", borderRadius: 4 }}
      >
        {t("account.logout")}
      </button>
      {logout.isError && (
        <div role="alert" style={{ marginTop: 8, padding: 8, background: "var(--surface-2)",
                                    borderRadius: 4, fontSize: 13, color: "var(--delay-severe)" }}>
          {t("account.logout_error")}
        </div>
      )}
      <div style={{ marginTop: 24, fontSize: "var(--text-sm)" }}>
        <LegalLinks />
      </div>
    </div>
  );
}
