import { ApiError } from "../api/client";

type Props = {
  error: unknown;
  onRetry?: () => void;
};

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "リクエストが多すぎます。しばらくお待ちください。";
    if (err.status === 404) return "データが見つかりませんでした。";
    if (err.status >= 500) return "サーバーエラーが発生しました。";
    return `エラー (${err.status})`;
  }
  if (err instanceof Error) return "接続エラー — ネットワークをご確認ください。";
  return "エラーが発生しました。";
}

export function ErrorBanner({ error, onRetry }: Props) {
  return (
    <div
      role="alert"
      style={{
        background: "var(--error-bg)",
        color: "var(--error-fg)",
        border: "1px solid #f0e2b6",
        padding: "10px 14px",
        borderRadius: "var(--radius)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        margin: "0 0 16px",
      }}
    >
      <span style={{ flex: 1 }}>{messageFor(error)}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            background: "transparent",
            border: "1px solid currentColor",
            color: "inherit",
            padding: "4px 10px",
            borderRadius: 4,
          }}
        >
          再試行
        </button>
      )}
    </div>
  );
}
