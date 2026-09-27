import { useAdminUsers } from "../../api/admin";

/** Who is waiting on an AI-access decision: not yet approved and still able
 *  to sign in. A suspended user -- every soft-deleted one included -- is
 *  blocked either way, so approving them is not the decision anyone is
 *  waiting on. The board's pending-approvals alert counts the same set
 *  server-side (`_PENDING_LLM_APPROVALS_SQL` in `api/routers/admin.py`).
 *  The nav badge, the Pending chip's count and the Pending view all read
 *  this one definition, so the count an operator sees is the number of rows
 *  the Pending view shows. */
export const PENDING_APPROVAL_FILTER = { llmApproved: "false", suspended: "false" } as const;

/** `total` counts every match, so this asks the server the question rather
 *  than filtering a page of rows -- a page-limited list stops counting once
 *  the table outgrows it, and the count silently undercounts from then on. */
export function usePendingApprovalCount(): number {
  const { data } = useAdminUsers({ ...PENDING_APPROVAL_FILTER, limit: 1, offset: 0 });
  return data?.total ?? 0;
}
