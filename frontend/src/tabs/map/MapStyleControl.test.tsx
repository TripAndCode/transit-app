import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import { MapStyleControl } from "./MapStyleControl";
import { buildThumbnailUrl, DEFAULT_THUMBNAIL_VIEW } from "../../styles/mapStyle";

function renderControl(overrides: Partial<Parameters<typeof MapStyleControl>[0]> = {}) {
  const onChange = vi.fn();
  const onDimChange = vi.fn();
  renderWithProviders(
    <MapStyleControl
      value="pale"
      onChange={onChange}
      dimAmount={0.3}
      onDimChange={onDimChange}
      t={i18n.t.bind(i18n)}
      {...overrides}
    />,
  );
  return { onChange, onDimChange };
}

describe("MapStyleControl", () => {
  it("expands to the style tiles and fires onChange with the chosen id", async () => {
    const { onChange } = renderControl();
    // Entry button is labelled "Map style" (aria-label) and shows "Layers".
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    // Expanded: a labelled tile per style.
    expect(screen.getByRole("button", { name: "Standard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Satellite" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "OSM" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Satellite" }));
    expect(onChange).toHaveBeenCalledWith("photo");
  });

  it("builds each tile's thumbnail from that style's own tile template (no mapRef → fallback view)", async () => {
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    const satellite = screen.getByRole("button", { name: "Satellite" });
    const img = satellite.querySelector("img");
    expect(img).toHaveAttribute("src", buildThumbnailUrl("photo", "ja", DEFAULT_THUMBNAIL_VIEW));
  });

  it("exposes the basemap dim as a slider and reports changes as a 0..0.6 fraction", async () => {
    const { onDimChange } = renderControl();
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    const slider = screen.getByRole("slider", { name: /dim/i });
    expect(slider).toHaveAttribute("max", "60");
    expect(slider).toHaveAttribute("value", "30");
    fireOnChange(slider, "45");
    expect(onDimChange).toHaveBeenCalledWith(0.45);
  });
});

// userEvent has no direct "set range value" helper that fires React's onChange
// reliably across jsdom versions; dispatching the native event is the
// documented workaround for range inputs.
function fireOnChange(el: Element, value: string) {
  const input = el as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("change", { bubbles: true }));
}
