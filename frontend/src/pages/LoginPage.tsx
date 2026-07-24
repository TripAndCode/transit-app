import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertCircle, Loader2 } from "lucide-react";
import { loginUrl } from "../api/auth";
import { useConfig } from "../api/config";
import { ApiError, apiPost } from "../api/client";
import "./LoginPage.css";

const ERROR_KEYS: Record<string, string> = {
  state: "account.login.error.state",
  unverified_email: "account.login.error.unverified_email",
  no_email: "account.login.error.no_email",
  provider_down: "account.login.error.provider_down",
  local_account_conflict: "account.login.error.local_account_conflict",
};

export function LoginPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const next = params.get("next") || "/";
  const error = params.get("error");
  const [pending, setPending] = useState<"google" | "github" | null>(null);
  const { data: config } = useConfig();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [localPending, setLocalPending] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = (provider: "google" | "github") => () => {
    setPending(provider);
    window.location.assign(loginUrl(provider, next));
  };

  async function handleLocalSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLocalPending(true);
    setLocalError(null);
    try {
      await apiPost("/api/auth/local/login", { username, password });
      // Full reload (not client-side navigate) so react-query's cached
      // /api/me resolves fresh against the session cookie just set.
      window.location.assign(next);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setLocalError(t("account.login.error.too_many_attempts"));
      } else if (err instanceof ApiError && err.status === 401) {
        setLocalError(t("account.login.error.invalid_credentials"));
      } else {
        setLocalError(t("account.login.error.generic"));
      }
      setLocalPending(false);
    }
  }

  if (config && !config.auth_enabled && !config.local_admin_enabled) {
    return (
      <div className="login-shell">
        <div className="login-shell__grid" aria-hidden="true" />
        <main className="login-card">
          <div className="login-card__brand">
            <span className="login-card__brand-title">{t("header.app_title")}</span>
            <span className="login-card__brand-tag">{t("header.app_tagline")}</span>
          </div>
          <h1 className="login-card__h1">{t("account.login.sso_disabled_title")}</h1>
          <p className="login-card__sub">
            {t("account.login.sso_disabled_body")}
          </p>
          <p className="login-card__footer">
            <Link to="/" style={{ color: "inherit" }}>{t("account.login.back_to_top")}</Link>
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="login-shell">
      <div className="login-shell__grid" aria-hidden="true" />
      <main className="login-card">
        <div className="login-card__brand">
          <span className="login-card__brand-title">{t("header.app_title")}</span>
          <span className="login-card__brand-tag">{t("header.app_tagline")}</span>
        </div>

        <h1 className="login-card__h1">{t("account.login.welcome_back")}</h1>
        <p className="login-card__sub">{t("account.login.choose_provider")}</p>

        {error && (
          <div className="login-card__error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{ERROR_KEYS[error] ? t(ERROR_KEYS[error]) : t("account.login.error.generic")}</span>
          </div>
        )}
        {localError && (
          <div className="login-card__error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{localError}</span>
          </div>
        )}

        {config?.auth_enabled && (
          <div className="login-card__buttons">
            <button
              type="button"
              className="login-card__btn login-card__btn--google"
              aria-label={t("account.login.google_aria")}
              aria-disabled={pending !== null}
              onClick={handleSubmit("google")}
            >
              {pending === "google" ? <Loader2 className="login-card__spinner" aria-hidden="true" /> : <GoogleMark />}
              <span>{t("account.login.google_continue")}</span>
            </button>
            <button
              type="button"
              className="login-card__btn login-card__btn--github"
              aria-label={t("account.login.github_aria")}
              aria-disabled={pending !== null}
              onClick={handleSubmit("github")}
            >
              {pending === "github" ? <Loader2 className="login-card__spinner" aria-hidden="true" /> : <GitHubMark />}
              <span>{t("account.login.github_continue")}</span>
            </button>
          </div>
        )}

        {config?.auth_enabled && config?.local_admin_enabled && (
          <div className="login-card__divider">{t("account.login.or_divider")}</div>
        )}

        {config?.local_admin_enabled && (
          <form className="login-card__local-form" onSubmit={handleLocalSubmit}>
            <div className="login-card__field">
              <label htmlFor="login-username">{t("account.login.username_label")}</label>
              <input
                id="login-username"
                name="username"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="login-card__field">
              <label htmlFor="login-password">{t("account.login.password_label")}</label>
              <input
                id="login-password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <button
              type="submit"
              className="login-card__btn login-card__btn--local"
              aria-disabled={localPending}
              disabled={localPending}
            >
              {localPending && <Loader2 className="login-card__spinner" aria-hidden="true" />}
              <span>{t("account.login.local_submit")}</span>
            </button>
          </form>
        )}

        {/*
          Terms paragraph: the inventory pre-split it into seven keys
          (prefix / link / and / link / suffix). We assemble those keys
          back into the sentence here so we don't need <Trans> for now.
          When we tighten the copy in a follow-up, we can collapse to a
          single `account.login.terms_paragraph` key with <terms> and
          <privacy> placeholders.
        */}
        <p className="login-card__footer">
          {t("account.login.terms_prefix")}
          <a href="/terms" target="_blank" rel="noreferrer">{t("account.login.terms_link")}</a>
          {t("account.login.terms_and")}
          <a href="/privacy" target="_blank" rel="noreferrer">{t("account.login.privacy_link")}</a>
          {t("account.login.terms_suffix")}
        </p>
      </main>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 16 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2c-2 1.4-4.5 2.2-7.2 2.2-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.2-4.2 5.6l6.2 5.2C40.7 35.5 44 30.2 44 24c0-1.2-.1-2.3-.4-3.5z" />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.04c-3.2.7-3.87-1.37-3.87-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.04-.71.08-.7.08-.7 1.16.08 1.77 1.2 1.77 1.2 1.03 1.77 2.7 1.26 3.36.96.1-.74.4-1.26.72-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.45.11-3.02 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.78 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.57.23 2.73.11 3.02.74.81 1.19 1.84 1.19 3.1 0 4.42-2.7 5.39-5.27 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.68.8.56 4.56-1.52 7.85-5.83 7.85-10.91C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}
