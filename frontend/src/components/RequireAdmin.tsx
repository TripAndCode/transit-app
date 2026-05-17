import { Navigate } from "react-router-dom";
import { useSession } from "../api/auth";

/** Route guard: redirects to ``/`` unless the current session is an admin. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { data: session, isLoading } = useSession();
  if (isLoading) return null;
  if (!session || session.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
