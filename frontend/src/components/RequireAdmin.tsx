import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "../api/auth";
import { GuardPlaceholder, ProbeError } from "./guardStates";

/** Route guard: sends unauthenticated callers to ``/login`` (keeping their URL as ``next``) and signed-in
 *  non-admins to ``/``. Only ``role=admin`` reaches the wrapped children. A session probe that fails
 *  before producing any value is shown with a retry rather than read as signed out, so a transient
 *  failure never sends an admin to sign in again. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const session = useSession();
  if (session.data === undefined) {
    return session.isError ? <ProbeError error={session.error} onRetry={() => void session.refetch()} /> : <GuardPlaceholder />;
  }
  if (!session.data) {
    const next = location.pathname + location.search + location.hash;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  if (session.data.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
