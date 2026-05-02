type Props = {
  title: string;
  hint?: string;
  hintMono?: string;
};

export function EmptyState({ title, hint, hintMono }: Props) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        color: "var(--text-secondary)",
        textAlign: "center",
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: "50%",
          background: "var(--bg-soft)",
          marginBottom: 16,
        }}
        aria-hidden
      />
      <div style={{ fontSize: 16, color: "var(--text-primary)" }}>{title}</div>
      {hint && <div style={{ marginTop: 8 }}>{hint}</div>}
      {hintMono && (
        <code
          style={{
            marginTop: 8,
            padding: "4px 8px",
            background: "var(--bg-soft)",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          {hintMono}
        </code>
      )}
    </div>
  );
}
