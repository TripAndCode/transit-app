import { Navigate, useLocation } from "react-router-dom";
import { useConfig } from "../api/config";
import { useSession } from "../api/auth";
import { ErrorBanner } from "./ErrorBanner";
import { Skeleton } from "./Skeleton";

/** Route guard for the app shell. When the API requires sign-in, a
 *  signed-out visitor at `/` goes to the landing page and anywhere else to
 *  `/login` with the whole URL as `next`, so a shared link survives the
 *  sign-in round trip. A probe that fails before producing any value is
 *  shown with a retry rather than guessed at: every tab behind this guard
 *  would only fail again. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const config = useConfig();
  const session = useSession();

  if (config.data === undefined) {
    return config.isError ? <ProbeError error={config.error} onRetry={() => void config.refetch()} /> : <GuardPlaceholder />;
  }
  if (!config.data.login_required) return <>{children}</>;
  if (session.data === undefined) {
    return session.isError ? <ProbeError error={session.error} onRetry={() => void session.refetch()} /> : <GuardPlaceholder />;
  }
  if (session.data) return <>{children}</>;
  if (location.pathname === "/") return <Navigate to="/welcome" replace />;
  const next = location.pathname + location.search + location.hash;
  return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
}

function ProbeError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div style={{ padding: 24 }}>
      <ErrorBanner error={error} onRetry={onRetry} />
    </div>
  );
}

function GuardPlaceholder() {
  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 12, maxWidth: 720 }}>
      <Skeleton height={28} width="40%" />
      <Skeleton height={200} />
    </div>
  );
}
