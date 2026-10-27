import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { RouteVariant } from "../../api/types";
import { RouteVariantPicker } from "./RouteVariantPicker";

function variant(shape_id: string, first: string, last: string, share_pct: number, samples = 10): RouteVariant {
  return { shape_id, first_stop_name: first, last_stop_name: last, stop_count: 22, samples, share_pct };
}

const OUT = variant("out", "広島駅", "観音新町三丁目", 31.8);
const IN = variant("in", "観音新町三丁目", "広島駅", 25);

describe("RouteVariantPicker", () => {
  it("offers nothing for a route with one path", () => {
    const { container } = renderWithProviders(<RouteVariantPicker variants={[OUT]} shown="out" onPick={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each path with its share and goes the other way on request", () => {
    const onPick = vi.fn();
    renderWithProviders(<RouteVariantPicker variants={[OUT, IN, variant("rare", "A", "B", 0.4)]} shown="out" onPick={onPick} />);
    expect(screen.getByRole("option", { name: "A → B (22 stops, 0.4% of observations)" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Other direction" }));
    expect(onPick).toHaveBeenCalledWith("in");
    fireEvent.change(screen.getByRole("combobox", { name: "Path" }), { target: { value: "rare" } });
    expect(onPick).toHaveBeenLastCalledWith("rare");
  });

  it("says a path with no observations in the window has none, rather than a share of 0%", () => {
    renderWithProviders(<RouteVariantPicker variants={[OUT, variant("pinned", "A", "B", 0, 0)]} shown="pinned" onPick={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Path" })).toHaveDisplayValue("A → B (22 stops, not observed in this period)");
    expect(screen.getByText("This path was not observed in this period")).toBeInTheDocument();
  });
});
