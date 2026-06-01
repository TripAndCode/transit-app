import { Navigate } from "react-router-dom";
import { useSession } from "../api/auth";
import { Skeleton } from "./Skeleton";

/** Route guard: sends unauthenticated callers to ``/login`` and signed-in
 *  non-admins to ``/``. Only ``role=admin`` reaches the wrapped children. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { data: session, isLoading } = useSession();
  if (isLoading) {
    // The session query is a useQuery, so ActivityStrip (mutation-only) won't
    // fire. Without a placeholder the admin page renders blank — the user
    // can't tell the gate is still resolving (R5 P1 regression fix).
    return (
      <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 12, maxWidth: 720 }}>
        <Skeleton height={28} width="40%" />
        <Skeleton height={200} />
      </div>
    );
  }
  if (!session) return <Navigate to="/login" replace />;
  if (session.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
