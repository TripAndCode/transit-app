import { useSearchParams } from "react-router-dom";
import { loginUrl } from "../api/auth";

const ERROR_COPY: Record<string, string> = {
  state: "ログインのリクエストが期限切れです。もう一度お試しください。",
  unverified_email: "メールアドレスが未確認です。プロバイダ側で確認を完了してください。",
  no_email: "GitHubの確認済みメールが取得できませんでした。プライマリメールを公開設定にするか、別の方法でログインしてください。",
  provider_down: "プロバイダ側に一時的な問題が発生しています。しばらくしてから再試行してください。",
};

/** OAuth provider chooser; surfaces error codes from the auth router. */
export function LoginPage() {
  const [params] = useSearchParams();
  const next = params.get("next") || "/";
  const error = params.get("error");
  return (
    <div style={{ maxWidth: 360, margin: "10vh auto", padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 24 }}>ログイン</h1>
      {error && (
        <div style={{ marginBottom: 16, padding: 12, background: "var(--surface-2)", borderRadius: 6 }}>
          {ERROR_COPY[error] ?? "ログインに失敗しました。"}
        </div>
      )}
      <a
        href={`${loginUrl("google", next)}`}
        style={{ display: "block", padding: "12px 16px", marginBottom: 12,
                 textAlign: "center", background: "var(--surface-1)", borderRadius: 6,
                 textDecoration: "none", color: "inherit" }}
      >
        Googleで続ける
      </a>
      <a
        href={`${loginUrl("github", next)}`}
        style={{ display: "block", padding: "12px 16px",
                 textAlign: "center", background: "var(--surface-1)", borderRadius: 6,
                 textDecoration: "none", color: "inherit" }}
      >
        GitHubで続ける
      </a>
    </div>
  );
}
