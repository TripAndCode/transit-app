import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { AdminTransferPair } from "../../api/admin";
import { AdminTransfersPage } from "./AdminTransfersPage";

const PAIR: AdminTransferPair = {
  id: 3,
  name: "Nukushina 29 to 2",
  from_agency_id: 9,
  from_route_code: "550332996",
  from_direction: null,
  from_stop_ids: ["50271 0"],
  to_agency_id: 8,
  to_route_code: "2136956995",
  to_direction: null,
  to_stop_ids: ["50271 1"],
  walk_sec: 120,
  day_types: ["weekday", "saturday", "sunday_holiday"],
  active: true,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const AGENCIES = [
  { agency_id: 8, agency_name: "Hiroden", feed_url: "", static_url: null, latest_data_date: null, capability_tier: "full" },
  { agency_id: 9, agency_name: "Hiroshima Bus", feed_url: "", static_url: null, latest_data_date: null, capability_tier: "full" },
];

type Call = { url: string; method: string; body: unknown };

function stub(saveStatus = 200, saveBody: unknown = PAIR) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const body = method !== "GET" ? saveBody : String(url).endsWith("/api/agencies") ? AGENCIES : [PAIR];
      const status = method === "GET" ? 200 : saveStatus;
      return Promise.resolve(
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
      );
    }),
  );
  return calls;
}

function open() {
  renderWithProviders(
    <MemoryRouter>
      <AdminTransfersPage />
    </MemoryRouter>,
  );
}

describe("AdminTransfersPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists the pairs with each side's agency name", async () => {
    stub();
    open();
    const row = (await screen.findByText("Nukushina 29 to 2")).closest("tr")!;
    await waitFor(() => expect(row).toHaveTextContent("Hiroshima Bus 550332996 (50271 0)"));
    expect(row).toHaveTextContent("Hiroden 2136956995 (50271 1)");
    expect(within(row).getByText("Active")).toBeInTheDocument();
  });

  it("edits a pair by sending its whole definition", async () => {
    const calls = stub();
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Walk between the stops (min)"), { target: { value: "3" } });
    fireEvent.click(screen.getByLabelText("Saturdays"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toContain("/api/admin/transfer-pairs/3");
    expect(patch.body).toEqual({
      name: PAIR.name,
      from_agency_id: 9,
      from_route_code: "550332996",
      from_direction: null,
      from_stop_ids: ["50271 0"],
      to_agency_id: 8,
      to_route_code: "2136956995",
      to_direction: null,
      to_stop_ids: ["50271 1"],
      walk_sec: 180,
      day_types: ["weekday", "sunday_holiday"],
      active: true,
    });
  });

  it("creates a pair from the form, splitting stop ids on commas", async () => {
    const calls = stub();
    open();
    await screen.findByText("Nukushina 29 to 2");
    const form = screen.getByRole("form", { name: "New transfer pair" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Hub" } });
    const [from, to] = within(form).getAllByRole("group").filter((g) => g.tagName === "FIELDSET").slice(0, 2);
    await waitFor(() => expect(within(from).getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(within(from).getByLabelText("Agency"), { target: { value: "9" } });
    fireEvent.change(within(from).getByLabelText("Route code"), { target: { value: "29" } });
    fireEvent.change(within(from).getByLabelText("Stop ids (comma-separated)"), { target: { value: "a, b ,," } });
    fireEvent.change(within(to).getByLabelText("Agency"), { target: { value: "8" } });
    fireEvent.change(within(to).getByLabelText("Route code"), { target: { value: "2" } });
    fireEvent.change(within(to).getByLabelText("Direction"), { target: { value: "1" } });
    fireEvent.change(within(to).getByLabelText("Stop ids (comma-separated)"), { target: { value: "c" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({
      name: "Hub",
      from_agency_id: 9,
      from_stop_ids: ["a", "b"],
      to_agency_id: 8,
      to_direction: 1,
      to_stop_ids: ["c"],
      walk_sec: 120,
    });
  });

  it("shows the server's reason when a save is refused", async () => {
    stub(422, { detail: "to_stop_ids: unknown stop ids ['zz']" });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("to_stop_ids: unknown stop ids ['zz']");
  });
});
