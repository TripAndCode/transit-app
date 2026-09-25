import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useApiKeys,
  useIssueApiKey,
  useRevokeApiKey,
  useRevokeSession,
  useUserSessions,
  type AdminApiKeyIssued,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { formatDateTime } from "../../utils/format";
import { AdminButton } from "./adminControls";

const rowStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: 8,
  background: "var(--surface-1)",
  borderRadius: 4,
  marginBottom: 4,
  fontSize: 13,
} as const;

/** Sessions list for one user, with per-session revoke. Sessions are
 * identified only by a display-safe prefix -- the full id is never fetched. */
export function SessionsSection({ uid }: { uid: number }) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useUserSessions(uid);
  const revoke = useRevokeSession(uid);

  return (
    <section style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("admin.user_detail.sessions_title")}</h2>
      {isLoading && <div>{t("common.loading")}</div>}
      {error && (
        <div role="alert" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(error)}
        </div>
      )}
      {data?.length === 0 && (
        <div style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{t("admin.user_detail.sessions_empty")}</div>
      )}
      {data?.map((s) => (
        <div key={s.sid_prefix} style={rowStyle}>
          <div>
            <code>{s.sid_prefix}…</code>
            <div style={{ color: "var(--text-tertiary)" }}>
              {t("admin.user_detail.session_last_seen", {
                time: formatDateTime(s.last_seen_at),
              })}
            </div>
          </div>
          <AdminButton
            variant="danger"
            disabled={revoke.isPending && revoke.variables === s.sid_prefix}
            onClick={() => revoke.mutate(s.sid_prefix)}
          >
            {t("admin.user_detail.session_revoke")}
          </AdminButton>
        </div>
      ))}
      {revoke.error && (
        <div role="alert" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(revoke.error)}
        </div>
      )}
    </section>
  );
}

/** API keys issued for one user: list + revoke, and an "issue" flow that
 * shows the raw key exactly once (in a copy box, from the mutation
 * response) -- it is never persisted client-side and never refetchable. */
export function ApiKeysSection({ uid }: { uid: number }) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useApiKeys(uid);
  const issue = useIssueApiKey(uid);
  const revoke = useRevokeApiKey(uid);
  const [justIssued, setJustIssued] = useState<AdminApiKeyIssued | null>(null);
  const [copied, setCopied] = useState(false);

  function handleIssue() {
    setCopied(false);
    issue.mutate({}, { onSuccess: (issued) => setJustIssued(issued) });
  }

  async function handleCopy() {
    if (!justIssued) return;
    try {
      await navigator.clipboard.writeText(justIssued.key);
      setCopied(true);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) -- the raw key
      // stays visible in the box below for a manual copy.
    }
  }

  return (
    <section style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("admin.user_detail.api_keys_title")}</h2>
      {justIssued && (
        <div style={{ marginBottom: 12, padding: 8, background: "var(--surface-1)", borderRadius: 4 }}>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 4 }}>
            {t("admin.user_detail.api_key_issued_notice")}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              readOnly
              value={justIssued.key}
              aria-label={t("admin.user_detail.api_key_issued_notice")}
              onFocus={(e) => e.currentTarget.select()}
              style={{ flex: 1, fontFamily: "monospace", fontSize: 12 }}
            />
            <AdminButton variant="secondary" onClick={handleCopy}>
              {copied ? t("admin.user_detail.api_key_copied") : t("admin.user_detail.api_key_copy")}
            </AdminButton>
          </div>
        </div>
      )}
      {isLoading && <div>{t("common.loading")}</div>}
      {error && (
        <div role="alert" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(error)}
        </div>
      )}
      {data?.keys.length === 0 && !justIssued && (
        <div style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{t("admin.user_detail.api_keys_empty")}</div>
      )}
      {data?.keys.map((k) => (
        <div key={k.id} style={rowStyle}>
          <div>
            {k.label ?? k.tier}
            {k.revoked_at && (
              <span style={{ marginLeft: 6, color: "var(--text-tertiary)" }}>
                ({t("admin.user_detail.api_key_revoked_label")})
              </span>
            )}
          </div>
          {!k.revoked_at && (
            <AdminButton
              variant="danger"
              disabled={revoke.isPending && revoke.variables === k.id}
              onClick={() => revoke.mutate(k.id)}
            >
              {t("admin.user_detail.api_key_revoke")}
            </AdminButton>
          )}
        </div>
      ))}
      <AdminButton variant="secondary" disabled={issue.isPending} onClick={handleIssue} style={{ marginTop: 8 }}>
        {t("admin.user_detail.api_key_issue")}
      </AdminButton>
      {issue.error && (
        <div role="alert" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(issue.error)}
        </div>
      )}
      {revoke.error && (
        <div role="alert" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(revoke.error)}
        </div>
      )}
    </section>
  );
}
