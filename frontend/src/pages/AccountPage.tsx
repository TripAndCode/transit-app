import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../api/auth";
import { apiDelete, apiErrorDetail, apiGet, apiPut } from "../api/client";
import { formatDateTime } from "../utils/format";
import { describeUserAgent } from "../utils/userAgent";
import { useAgencies } from "../api/hooks";
import { readLastAgency } from "../api/lastAgency";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Section } from "../components/ui/Section";
import { Toolbar } from "../components/ui/Toolbar";
import { LegalLinks } from "../components/LegalLinks";
import { Modal } from "../components/Modal";
import { Skeleton } from "../components/Skeleton";
import "./AccountPage.css";

type SessionRow = {
  sid_prefix: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  /** The session this page was loaded with. */
  current: boolean;
};

type LlmKeyStatus = {
  configured: boolean;
  provider?: string;
  key_suffix?: string;
};

/** BYOK LLM key settings — lets a signed-in user store their own provider key
 * so Copilot/Ask calls bill their provider account, not the operator's. The
 * raw key is write-only: the backend never echoes it back, only the masked
 * `key_suffix`, and this component never holds it in state past the mutation
 * call that sends it. */
function LlmKeySection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ["myLlmKey"],
    queryFn: ({ signal }) => apiGet<LlmKeyStatus>("/api/me/llm-key", { signal }),
  });
  const [providerOverride, setProviderOverride] = useState<string | null>(null);
  const provider = providerOverride ?? status?.provider ?? "gemini";
  const [apiKey, setApiKey] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => apiPut<LlmKeyStatus>("/api/me/llm-key", { provider, api_key: apiKey }),
    onSuccess: (data) => {
      setApiKey("");
      setSaveError(null);
      // Seed the cache directly from the mutation response rather than just
      // invalidating: the response already carries the fresh masked
      // `key_suffix` (never the raw key), so this shows the new status
      // immediately without waiting on a second round-trip refetch.
      qc.setQueryData(["myLlmKey"], data);
    },
    onError: () => {
      setApiKey("");
      setSaveError(t("account.llm_key.rejected"));
    },
  });

  const remove = useMutation({
    mutationFn: () => apiDelete("/api/me/llm-key"),
    onSuccess: () => {
      setRemoveError(null);
      qc.setQueryData(["myLlmKey"], { configured: false });
    },
    onError: () => setRemoveError(t("account.llm_key.remove_error")),
  });

  return (
    <Section
      title={t("account.llm_key.title")}
      description={
        status?.configured
          ? t("account.llm_key.status_own", { provider: status.provider, suffix: status.key_suffix })
          : t("account.llm_key.status_shared")
      }
    >
      <Toolbar>
        <select value={provider} onChange={(e) => setProviderOverride(e.target.value)}>
          <option value="gemini" /* i18n-ignore: provider brand name */>Gemini</option>
          <option value="openai" /* i18n-ignore: provider brand name */>OpenAI</option>
        </select>
        <label>
          {t("account.llm_key.input_label")}
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </label>
        <button onClick={() => save.mutate()} disabled={!apiKey || save.isPending}>
          {t("common.save")}
        </button>
        {status?.configured && (
          <button onClick={() => remove.mutate()} disabled={remove.isPending}>
            {t("account.llm_key.remove")}
          </button>
        )}
      </Toolbar>
      {saveError && <p role="alert">{saveError}</p>}
      {removeError && <p role="alert">{removeError}</p>}
    </Section>
  );
}

const DELETE_ERROR_KEYS: Record<string, string> = {
  last_admin: "account.data.error_last_admin",
  managed_account: "account.data.error_managed",
};

/** Download everything the app holds about you. */
function DataSection() {
  const { t } = useTranslation();
  return (
    <Section title={t("account.data.title")} description={t("account.data.description")}>
      <Toolbar>
        <a href="/api/me/export" download>
          {t("account.data.export")}
        </a>
      </Toolbar>
    </Section>
  );
}

/** Delete the account for good, apart from everything else on the page.
 *  Deletion is a hard delete on the server, so the confirm button waits for
 *  the account's own email to be typed. */
function DangerZone({ email }: { email: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: () => apiDelete("/api/me", { body: { confirm_email: typed } }),
    onSuccess: () => {
      qc.clear();
      navigate("/welcome", { replace: true });
    },
  });
  const matches = typed.trim().toLowerCase() === email.toLowerCase();
  const detail = remove.error ? apiErrorDetail(remove.error) : null;
  const errorKey = remove.error ? (DELETE_ERROR_KEYS[detail ?? ""] ?? "account.data.error_generic") : null;

  return (
    <Section className="account-danger" title={t("account.data.delete")} description={t("account.data.delete_description")}>
      <Toolbar>
        <button
          className="account-danger__button"
          onClick={() => {
            setTyped("");
            remove.reset();
            setOpen(true);
          }}
        >
          {t("account.data.delete")}
        </button>
      </Toolbar>
      <Modal
        open={open}
        onClose={() => {
          if (!remove.isPending) setOpen(false);
        }}
        labelledBy={titleId}
        style={{
          width: "min(460px, calc(100vw - 32px))",
          padding: 20,
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--el-2)",
        }}
      >
        <h2 id={titleId} style={{ margin: "0 0 8px", fontSize: 16 }}>
          {t("account.data.delete_title")}
        </h2>
        <p style={{ margin: "0 0 12px", fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
          {t("account.data.delete_body")}
        </p>
        <label style={{ display: "block", marginBottom: 12, fontSize: "var(--text-sm)" }}>
          {t("account.data.delete_confirm_label", { email })}
          <input value={typed} onChange={(e) => setTyped(e.target.value)} style={{ display: "block", width: "100%", marginTop: 4 }} />
        </label>
        {errorKey && (
          <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
            {t(errorKey)}
          </p>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={() => setOpen(false)} disabled={remove.isPending}>
            {t("common.cancel")}
          </button>
          <button className="account-danger__button" onClick={() => remove.mutate()} disabled={!matches || remove.isPending}>
            {t("account.data.delete_confirm")}
          </button>
        </div>
      </Modal>
    </Section>
  );
}

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
  const { data: sessions } = useQuery({
    queryKey: ["mySessions"],
    queryFn: ({ signal }) => apiGet<SessionRow[]>("/api/me/sessions", { signal }),
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
  if (!session) return <Navigate to="/login" replace />;

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
      <LlmKeySection />
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
        {signOutSession.isError && (
          <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
            {t("account.session_sign_out_error")}
          </p>
        )}
      </Section>
      <DataSection />
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
      <DangerZone email={session.email} />
      <div style={{ marginTop: 24, fontSize: "var(--text-sm)" }}>
        <LegalLinks />
      </div>
    </div>
  );
}
