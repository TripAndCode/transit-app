import { Navigate } from "react-router-dom";
import { useSession } from "../api/auth";

/** Route guard: sends unauthenticated callers to ``/login`` and signed-in
 *  non-admins to ``/``. Only ``role=admin`` reaches the wrapped children. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { data: session, isLoading } = useSession();
  if (isLoading) return null;
  if (!session) return <Navigate to="/login" replace />;
  if (session.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
