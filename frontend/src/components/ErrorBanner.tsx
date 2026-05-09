import { ApiError } from "../api/client";

type Props = {
  error: unknown;
  onRetry?: () => void;
};

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "アクセスが集中しています。少し待って再試行してください。";
    if (err.status === 404) return "対象のデータがまだありません。";
    if (err.status >= 500) return "一時的に取得できませんでした。再試行してください。";
    return `読み込みに失敗しました (${err.status})`;
  }
  if (err instanceof Error) return "通信が一時的に途切れました。再試行してください。";
  return "読み込みに失敗しました。";
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
          type="button"
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
