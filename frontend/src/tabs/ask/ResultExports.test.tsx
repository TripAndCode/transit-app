import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import type { ConvMessage } from "../../api/types";
import * as csv from "../../components/analysis/csv";
import { ResultExports } from "./ResultExports";
import * as snapshots from "./resultSnapshot";

const message: ConvMessage = {
  message_id: 2, conversation_id: "thread", role: "assistant", chip_id: null,
  tool: null, args: null, signature_hash: null, result: null,
  rendered_summary: "Answer", created_at: "2026-09-01T00:00:00Z",
};
const step = { id: 1, question: "Question?", messages: [message] };

afterEach(() => vi.restoreAllMocks());

describe("result exports", () => {
  it("exports only the selected step and handles download failure", () => {
    const download = vi.spyOn(snapshots, "downloadSnapshot").mockImplementation(() => { throw new Error("unavailable"); });
    renderWithProviders(<ResultExports agencyId={9} step={step} />);
    fireEvent.click(screen.getByRole("button", { name: "Download result (JSON)" }));
    expect(download).toHaveBeenCalledWith(9, step);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not create the download");
    download.mockImplementation(() => {});
    fireEvent.click(screen.getByRole("button", { name: "Download result (JSON)" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
  });
  it("does not export an unanswered question", () => {
    renderWithProviders(<ResultExports agencyId={9} step={{ ...step, messages: [{ ...message, role: "user" }] }} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("credits the agency's data source in each table CSV", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [{ agency_id: 9, agency_name: "Hiroden", feed_url: "https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin" }],
    } as never);
    const download = vi.spyOn(csv, "downloadCsv").mockImplementation(() => {});
    const table: ConvMessage = {
      ...message,
      result: { kind: "table", summary: "Table", columns: ["route"], rows: [["C10"]], series: null, pairs: null },
    };
    renderWithProviders(<ResultExports agencyId={9} step={{ ...step, messages: [table] }} />);
    fireEvent.click(screen.getByRole("button", { name: /CSV/ }));
    const rows = download.mock.calls[0][1];
    expect(rows).toContainEqual([
      "data_source",
      "This service uses, with modifications, the data of each operator in Hiroshima Prefecture that the " +
        "Hiroshima Bus Association publishes on its GTFS open data site (https://www.bus-kyo.or.jp/) under " +
        "Creative Commons CC0 (https://creativecommons.org/publicdomain/zero/1.0/deed.en).",
    ]);
  });
});
