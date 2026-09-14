import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { ConvMessage } from "../../api/types";
import { ResultExports } from "./ResultExports";
import * as snapshots from "./resultSnapshot";

const message: ConvMessage = {
  message_id: 2, conversation_id: "thread", role: "assistant", chip_id: null,
  tool: null, args: null, signature_hash: null, result: null,
  rendered_summary: "Answer", created_at: "2026-09-01T00:00:00Z",
};
const step = { id: 1, question: "Question?", messages: [message] };

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
});
