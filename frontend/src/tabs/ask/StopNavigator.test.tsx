import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { StopEvidenceChart } from "./StopEvidenceChart";
import type { StopEvidence } from "./stopEvidence";

const points: StopEvidence[] = Array.from({ length: 64 }, (_, index) => ({
  sequence: index + 1, name: `Stop ${index + 1}`, stopId: `s${index + 1}`, patternId: "p",
  minutes: index === 60 ? null : index % 5, samples: index === 60 ? 0 : 8, rowIndex: index,
}));

describe("stop navigator", () => {
  it("bounds the main plot, pans locally and keeps the full pattern count", () => {
    const focus = vi.fn();
    renderWithProviders(<StopEvidenceChart complete messageId={9} points={points} onFocus={focus} />);
    expect(screen.getByText("Showing 1–8 / 64 stops")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Stop 64, sequence/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("slider"), { target: { value: "56" } });
    expect(screen.getByText("Showing 57–64 / 64 stops")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Stop 61, sequence 61: no departure/ })).toBeInTheDocument();
    expect(focus).toHaveBeenLastCalledWith(null);
  });
  it("searches beyond row 50 and selects the original row, not the viewport offset", () => {
    const focus = vi.fn();
    renderWithProviders(<StopEvidenceChart complete messageId={9} points={points} onFocus={focus} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Ｓｔｏｐ ６１" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop 61 · #61" }));
    expect(focus).toHaveBeenLastCalledWith({ messageId: 9, sequence: 61, name: "Stop 61", patternId: "p", stopId: "s61", rowIndex: 60 });
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
  it("can show all stops without turning missing observations into bars", () => {
    const { container } = renderWithProviders(<StopEvidenceChart complete messageId={9} points={points} />);
    fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "64" } });
    expect(screen.getByRole("slider")).toBeDisabled();
    expect(container.querySelectorAll(".stop-evidence-bar")).toHaveLength(63);
    expect(container.querySelectorAll(".stop-evidence-column")).toHaveLength(64);
  });
});
