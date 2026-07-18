import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Search } from "lucide-react";

/** Guards against duplicate <style> injection across Strict Mode double-invoke
 *  and HMR remounts — same pattern as ActivityStrip's _stripStylesInjected. */
let _adminStylesInjected = false;

const ADMIN_CSS = `
  .admin-btn {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 13px; font-weight: 500; padding: 6px 13px;
    border-radius: 6px; cursor: pointer; font-family: inherit;
    border: 1px solid transparent; background: transparent;
    transition: background 150ms ease, border-color 150ms ease;
  }
  .admin-btn:disabled { opacity: 0.5; cursor: default; }
  .admin-btn.secondary { border-color: var(--border-subtle); color: var(--text-primary); }
  .admin-btn.secondary:hover:not(:disabled) { background: var(--hover-tint); }
  .admin-btn.danger { border-color: var(--border-subtle); color: var(--color-danger, #c0392b); }
  .admin-btn.danger:hover:not(:disabled) { background: var(--bg-soft); border-color: var(--color-danger, #c0392b); }
  .admin-btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .admin-btn.primary:hover:not(:disabled) { opacity: 0.9; }

  table.admin-table { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.admin-table thead tr { background: var(--surface-1); }
  table.admin-table th {
    padding: 9px 12px; text-align: left; font-weight: 600; font-size: 12px;
    color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.03em;
  }
  table.admin-table td { padding: 10px 12px; }
  table.admin-table tbody tr { border-bottom: 1px solid var(--surface-2); }
  table.admin-table tbody tr:hover td { background: var(--hover-tint); }
  table.admin-table a { color: inherit; text-decoration: none; }
  table.admin-table a:hover { text-decoration: underline; }

  .admin-search { position: relative; margin-bottom: 18px; max-width: 320px; }
  .admin-search svg { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-tertiary); pointer-events: none; }
  .admin-search input { width: 100%; padding: 8px 12px 8px 34px; }
`;

/** Injects the shared admin-section stylesheet into <head> exactly once per
 *  module lifetime — needed for :hover states that inline style objects
 *  can't express. */
function useAdminStyles(): void {
  useEffect(() => {
    if (_adminStylesInjected) return;
    const el = document.createElement("style");
    el.textContent = ADMIN_CSS;
    document.head.appendChild(el);
    _adminStylesInjected = true;
  }, []);
}

type AdminButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant: "primary" | "secondary" | "danger";
};

/** Shared row/toolbar action button for the admin section (Agencies/Users/
 *  Ops previously each left these as unstyled native <button>s). */
export function AdminButton({ variant, className, type = "button", ...rest }: AdminButtonProps) {
  useAdminStyles();
  return <button type={type} className={`admin-btn ${variant} ${className ?? ""}`} {...rest} />;
}

/** Avatar-initial circle for a table row (email/name-keyed identity), matching
 *  the treatment already used by SidebarUserMenu's account trigger. */
export function AdminAvatar({ label }: { label: string }) {
  useAdminStyles();
  return (
    <span
      aria-hidden="true"
      style={{
        width: 26,
        height: 26,
        borderRadius: "50%",
        flexShrink: 0,
        background: "var(--accent-soft)",
        color: "var(--accent)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 11,
        fontWeight: 700,
        marginRight: 9,
        verticalAlign: "middle",
      }}
    >
      {label.slice(0, 1).toUpperCase()}
    </span>
  );
}

/** Color-coded status pill: "good" (accent), "warn" (amber), "neutral" (muted
 *  gray) — the one shared shape behind Users' Active/Suspended, Agencies'
 *  Active/Deleted, and Ops' Fresh/behind/never chips. */
export function StatusChip({ tone, children }: { tone: "good" | "warn" | "neutral"; children: ReactNode }) {
  useAdminStyles();
  const styles = {
    good: { background: "var(--accent-soft)", color: "var(--accent)" },
    warn: { background: "var(--surface-2)", color: "var(--color-warning, #C99A2E)" },
    neutral: { background: "var(--surface-2)", color: "var(--text-tertiary)" },
  }[tone];
  return (
    <span style={{ fontSize: 12, fontWeight: 500, padding: "2px 9px", borderRadius: 999, ...styles }}>
      {children}
    </span>
  );
}

/** Search input with a leading icon, replacing the bare <input type="search">
 *  used by AdminUsersPage. */
export function AdminSearchInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  useAdminStyles();
  return (
    <div className="admin-search">
      <Search size={14} strokeWidth={2} aria-hidden="true" />
      <input type="search" {...props} />
    </div>
  );
}
