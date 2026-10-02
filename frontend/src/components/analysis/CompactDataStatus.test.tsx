import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as hooks from "../../api/hooks";
import * as agencyIdModule from "../../api/useAgencyId";
import { CompactDataStatus } from "./CompactDataStatus";

describe("CompactDataStatus", () => {
  it("shows the newest aggregated day, not today's live day", () => {
    vi.spyOn(agencyIdModule, "useAgencyId").mockReturnValue(1);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [{ agency_id: 1, latest_data_date: "2026-10-01" }] } as never);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { date: "2026-10-02", latest_captured_at: null, routes: [], raw_samples: 0, clamp_count: 0 },
      error: null,
      refetch: vi.fn(),
    } as never);
    render(<CompactDataStatus />);
    expect(screen.getByText(/2026-10-01/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-10-02/)).toBeNull();
  });
});
