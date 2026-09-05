import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "./CopilotPanel";
import * as client from "../api/client";

function renderPanel(path: string) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <CopilotPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CopilotPanel", () => {
  it("shows a step-back note on the Ask tab instead of calling the insight endpoint", () => {
    const spy = vi.spyOn(client, "apiPost");
    renderPanel("/agencies/1/ask");
    // Bilingual match — jsdom's detected language isn't pinned here (unlike
    // renderWithProviders, which forces "en"), so this must tolerate either
    // resource bundle resolving, matching the existing ErrorBanner.test.tsx
    // convention for un-pinned-locale assertions.
    expect(screen.getByText(/こちらで会話が続いています|already in the full conversation/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the fetched insight text on the Overview tab", async () => {
    // The panel only calls the insight endpoint once it has a view_payload,
    // which here comes from the real useOverviewSummary hook — so its
    // underlying apiGet must resolve too, not just apiPost.
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    });
    renderPanel("/agencies/1/overview");
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy(), { timeout: 2000 });
  });
});
