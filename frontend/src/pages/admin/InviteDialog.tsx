import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "../../components/Modal";
import { useCreateInvite } from "../../api/admin";
import { formatApiError } from "../../api/client";

type Props = { open: boolean; onClose: () => void };

/** "＋ 招待" dialog: pre-approve a role (and optionally AI access) for an
 * email that hasn't signed in yet. Honored by the OAuth callback on that
 * email's first login. */
export function InviteDialog({ open, onClose }: Props) {
  // Mounted only while open, so the form starts empty every time rather
  // than showing the last invite's values.
  if (!open) return null;
  return <InviteDialogBody onClose={onClose} />;
}

function InviteDialogBody({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [llmApproved, setLlmApproved] = useState(false);
  const create = useCreateInvite();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    create.mutate(
      { email: email.trim(), role, llm_approved: llmApproved },
      {
        onSuccess: () => {
          setEmail("");
          setRole("user");
          setLlmApproved(false);
        },
      },
    );
  }

  return (
    <Modal open onClose={onClose} ariaLabel={t("admin.invite.dialog_title")} style={{ width: 360 }}>
        <h3 style={{ marginTop: 0 }}>{t("admin.invite.dialog_title")}</h3>
        <form onSubmit={handleSubmit}>
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ marginBottom: 4, color: "var(--text-secondary)", fontSize: 13 }}>
              {t("admin.invite.email_label")}
            </div>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ width: "100%" }}
            />
          </label>
          <label htmlFor="invite-role" style={{ display: "block", marginBottom: 12 }}>
            <div style={{ marginBottom: 4, color: "var(--text-secondary)", fontSize: 13 }}>
              {t("admin.invite.role_label")}
            </div>
            <select
              id="invite-role"
              value={role}
              onChange={(e) => setRole(e.target.value as "user" | "admin")}
              style={{ width: "100%" }}
            >
              <option value="user">{t("account.role.user")}</option>
              <option value="admin">{t("account.role.admin")}</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            <input
              type="checkbox"
              checked={llmApproved}
              onChange={(e) => setLlmApproved(e.target.checked)}
            />
            {t("admin.invite.llm_approved_label")}
          </label>
          {create.isSuccess && (
            <div style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>
              {t("admin.invite.success", { email: create.data.email })}
            </div>
          )}
          {create.error && (
            <div role="alert" style={{ marginBottom: 12, fontSize: 13, color: "var(--text-tertiary)" }}>
              {t("admin.invite.error", { msg: formatApiError(create.error) })}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: "transparent",
                color: "var(--text-primary)",
                border: "1px solid var(--border-subtle)",
                padding: "6px 12px",
                borderRadius: 4,
              }}
            >
              {t("common.cancel")}
            </button>
            <button
              type="submit"
              disabled={create.isPending}
              style={{
                background: "var(--accent)",
                color: "var(--on-accent)",
                border: "none",
                padding: "6px 14px",
                borderRadius: 4,
              }}
            >
              {t("admin.invite.submit")}
            </button>
          </div>
        </form>
    </Modal>
  );
}
