type Props = {
  title: string;
  hint?: string;
};

export function EmptyState({ title, hint }: Props) {
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
    </div>
  );
}
