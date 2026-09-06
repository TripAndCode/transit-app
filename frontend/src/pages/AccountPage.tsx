import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../api/auth";
import { apiDelete, apiGet, apiPut } from "../api/client";

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
 * so Copilot/Ask calls use it instead of the shared operator key + quota. The
 * raw key is write-only: the backend never echoes it back, only the masked
 * `key_suffix`, and this component never holds it in state past the mutation
 * call that sends it. */
function LlmKeySection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ["myLlmKey"],
    queryFn: () => apiGet<LlmKeyStatus>("/api/me/llm-key"),
  });
  const [provider, setProvider] = useState("groq");
  const [apiKey, setApiKey] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);

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
    onError: () => setSaveError(t("account.llm_key.rejected")),
  });

  const remove = useMutation({
    mutationFn: () => apiDelete("/api/me/llm-key"),
    onSuccess: () => qc.setQueryData(["myLlmKey"], { configured: false }),
  });

  return (
    <section style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.llm_key.title")}</h2>
      <p>
        {status?.configured
          ? t("account.llm_key.status_own", { provider: status.provider, suffix: status.key_suffix })
          : t("account.llm_key.status_shared")}
      </p>
      <select value={provider} onChange={(e) => setProvider(e.target.value)}>
        <option value="groq">Groq</option>
        <option value="openai">OpenAI</option>
        <option value="cerebras">Cerebras</option>
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
      {saveError && <p role="alert">{saveError}</p>}
    </section>
  );
}

/** Self-service profile + active sessions + logout. */
export function AccountPage() {
  const { t, i18n } = useTranslation();
  const { data: session, isLoading } = useSession();
  const { data: sessions } = useQuery({
    queryKey: ["mySessions"],
    queryFn: () => apiGet<SessionRow[]>("/api/me/sessions"),
  });
  const logout = useLogout();

  if (isLoading) return <div style={{ padding: 24 }}>{t("common.loading")}</div>;
  if (!session) return <Navigate to="/login" replace />;

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("account.title")}</h1>
      <section style={{ marginBottom: 24 }}>
        <div>{session.email}</div>
        <div style={{ color: "var(--text-tertiary)" }}>{session.name ?? ""}</div>
        <div style={{ color: "var(--text-tertiary)", fontSize: 13, marginTop: 4 }}>
          {t("account.role_label")}: {session.role === "admin" ? t("account.role.admin") : t("account.role.user")}
        </div>
      </section>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.linked_providers")}</h2>
        <ul>{session.identities.map((i) => <li key={i.provider}>{i.provider}</li>)}</ul>
      </section>
      <LlmKeySection />
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.active_sessions")}</h2>
        {sessions?.map((s) => (
          <div key={s.sid_prefix} style={{ padding: 8, background: "var(--surface-1)",
                                            borderRadius: 4, marginBottom: 4, fontSize: 13 }}>
            <div>{s.user_agent ?? "(unknown UA)"}</div>
            <div style={{ color: "var(--text-tertiary)" }}>
              {t("account.session_last_seen", { when: new Date(s.last_seen_at).toLocaleString(i18n.language) })}
            </div>
          </div>
        ))}
      </section>
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
