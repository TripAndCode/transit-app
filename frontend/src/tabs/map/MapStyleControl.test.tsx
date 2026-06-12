import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import { MapStyleControl } from "./MapStyleControl";

describe("MapStyleControl", () => {
  it("expands to 3 options and fires onChange with the chosen id", async () => {
    const onChange = vi.fn();
    renderWithProviders(<MapStyleControl value="pale" onChange={onChange} t={i18n.t.bind(i18n)} />);
    await userEvent.click(screen.getByRole("button", { name: /Light|Map style/ }));
    expect(screen.getByRole("button", { name: "Standard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Satellite" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Satellite" }));
    expect(onChange).toHaveBeenCalledWith("photo");
  });
});
