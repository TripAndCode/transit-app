import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../api/auth";
import { apiDelete, apiGet, apiPut } from "../api/client";
import { formatDateTime } from "../utils/format";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Section } from "../components/ui/Section";
import { Toolbar } from "../components/ui/Toolbar";

type SessionRow = {
  sid_prefix: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
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
          <option value="gemini">Gemini</option>
          <option value="openai">OpenAI</option>
        </select>
        <label>
          {t("account.llm_key.input_label")}
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </label>
        <button onClick={() => save.mutate()} disabled={!apiKey || save.isPending}>
          {t("account.llm_key.save")}
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

/** Self-service profile + active sessions + logout. */
export function AccountPage() {
  const { t } = useTranslation();
  const { data: session, isLoading } = useSession();
  const { data: sessions } = useQuery({
    queryKey: ["mySessions"],
    queryFn: ({ signal }) => apiGet<SessionRow[]>("/api/me/sessions", { signal }),
  });
  const logout = useLogout();

  if (isLoading) return <div style={{ padding: 24 }}>{t("common.loading")}</div>;
  if (!session) return <Navigate to="/login" replace />;

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      <PageHeader title={t("account.title")} subtitle={session.email} />
      <Card style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--text-tertiary)" }}>{session.name ?? ""}</div>
        <div style={{ color: "var(--text-tertiary)", fontSize: "var(--text-sm)", marginTop: 4 }}>
          {t("account.role_label")}: {session.role === "admin" ? t("account.role.admin") : t("account.role.user")}
        </div>
      </Card>
      <Section title={t("account.linked_providers")}>
        <ul>{session.identities.map((i) => <li key={i.provider}>{i.provider}</li>)}</ul>
      </Section>
      <LlmKeySection />
      <Section title={t("account.active_sessions")}>
        {sessions?.map((s) => (
          <Card key={s.sid_prefix} style={{ marginBottom: 6, fontSize: "var(--text-sm)" }}>
            <div>{s.user_agent ?? "(unknown UA)"}</div>
            <div style={{ color: "var(--text-tertiary)" }}>
              {t("account.session_last_seen", { when: formatDateTime(s.last_seen_at) })}
            </div>
          </Card>
        ))}
      </Section>
      <button
        onClick={() => logout.mutate(undefined, { onSuccess: () => (window.location.href = "/") })}
        disabled={logout.isPending}
        style={{ padding: "8px 16px", background: "var(--surface-2)", color: "var(--text-primary)", border: "none", borderRadius: 4 }}
      >
        {t("account.logout")}
      </button>
      {logout.isError && (
        <div role="alert" style={{ marginTop: 8, padding: 8, background: "var(--surface-2)",
                                    borderRadius: 4, fontSize: 13, color: "var(--color-danger, #c0392b)" }}>
          {t("account.logout_error")}
        </div>
      )}
    </div>
  );
}
