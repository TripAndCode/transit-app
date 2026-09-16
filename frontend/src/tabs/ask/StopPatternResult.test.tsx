import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { renderWithProviders } from "../../test/renderWithProviders";
import { StopPatternResult } from "./StopPatternResult";
import { stopEvidence } from "./stopEvidence";
import { FollowupChipsRow } from "./FollowupChipsRow";

const source = { message_id: 99, role: "assistant", tool: "route_stop_patterns", result: {
  kind: "table", columns: ["pattern_id", "pattern_name", "stop_sequence", "stop_id", "stop_name", "avg_min", "samples"],
  rows: [["a", "A → B", 1, "A", "Central", 0, 2], ["a", "A → B", 2, "B", "Park", null, 0],
    ["b", "B → A", 1, "B", "Park", 4, 6]],
} } as ConvMessage;

describe("complete stop patterns", () => {
  it("keeps zero delay distinct from missing, and the same sequence in different patterns", () => {
    const points = stopEvidence(source)!;
    expect(points).toHaveLength(3);
    expect(points[0].minutes).toBe(0);
    expect(points[1].minutes).toBeNull();
    expect(points[1].rowIndex).toBe(1);
    expect(points[2].patternId).toBe("b");
  });
  it("selects missing stops without inventing a measurement, clearing focus on pattern changes", () => {
    const onFocus = vi.fn();
    renderWithProviders(<StopPatternResult messageId={99} points={stopEvidence(source)!} onFocus={onFocus} />);
    fireEvent.click(screen.getByRole("button", { name: /Park, sequence 2: no departure/ }));
    expect(onFocus).toHaveBeenLastCalledWith({ messageId: 99, sequence: 2, name: "Park", patternId: "a", stopId: "B", rowIndex: 1 });
    expect(screen.getByText("0 observations")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Observed stop pattern" }), { target: { value: "b" } });
    expect(onFocus).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole("button", { name: /Park, sequence 1: 4 minutes/ })).toBeInTheDocument();
  });
  it("passes the exact stored row index to the explicit follow-up action", () => {
    const send = vi.fn();
    function Composer() {
      const { t } = useTranslation();
      return <FollowupChipsRow messages={[source]} t={t} compact draftValue="Explain" onDraftChange={() => {}}
        onFollowup={send} focus={{ messageId: 99, sequence: 2, name: "Park", patternId: "a", stopId: "B", rowIndex: 1 }} />;
    }
    renderWithProviders(<Composer />);
    expect(send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(send).toHaveBeenCalledWith(99, "Explain\nSelected: Park (sequence 2).", true, 1);
  });
});
