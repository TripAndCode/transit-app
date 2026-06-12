import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import { MapStyleControl } from "./MapStyleControl";

describe("MapStyleControl", () => {
  it("expands to the style tiles and fires onChange with the chosen id", async () => {
    const onChange = vi.fn();
    renderWithProviders(<MapStyleControl value="pale" onChange={onChange} t={i18n.t.bind(i18n)} />);
    // Entry button is labelled "Map style" (aria-label) and shows "Layers".
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    // Expanded: a labelled tile per style.
    expect(screen.getByRole("button", { name: "Standard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Satellite" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "OSM" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Satellite" }));
    expect(onChange).toHaveBeenCalledWith("photo");
  });
});
