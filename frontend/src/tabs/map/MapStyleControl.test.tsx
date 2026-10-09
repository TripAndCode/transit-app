import { describe, it, expect, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import { MapStyleControl } from "./MapStyleControl";
import { buildThumbnailUrl, DEFAULT_THUMBNAIL_VIEW } from "../../styles/mapStyle";
import type { Map as MLMap } from "maplibre-gl";

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
    expect(screen.getByRole("button", { name: "OpenStreetMap" })).toBeInTheDocument();
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

  // The map stays draggable behind this overlay, so the view the thumbnails
  // were built from can go stale at any point the panel isn't being toggled.
  // Both transitions resample; the entry thumbnail is visible either way.
  it("resamples the map view on open and on close", async () => {
    let center = { lng: 139.7, lat: 35.7 };
    let zoom = 11;
    const mapRef = {
      current: { getCenter: () => center, getZoom: () => zoom },
    } as unknown as React.MutableRefObject<MLMap | null>;
    renderControl({ mapRef });
    const entry = screen.getByRole("button", { name: /Map style|Layers/ });

    await userEvent.click(entry);
    expect(entry.querySelector("img")).toHaveAttribute(
      "src",
      buildThumbnailUrl("pale", "ja", { ...center, zoom }),
    );

    // Pan while the panel is open, then close via a tile: the entry thumbnail
    // must follow the map rather than stay at the open-time position.
    center = { lng: 135.5, lat: 34.7 };
    zoom = 13;
    await userEvent.click(screen.getByRole("button", { name: "Satellite" }));
    expect(entry.querySelector("img")).toHaveAttribute(
      "src",
      buildThumbnailUrl("pale", "ja", { ...center, zoom }),
    );
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

  it("renders layer chips inside the panel, pressed when on, and toggles through the callback", async () => {
    const onToggle = vi.fn();
    renderControl({ layers: [{ id: "relief", label: "Relief", hint: "Column height = average delay", on: true, onToggle, icon: <span /> }] });
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    const chip = screen.getByRole("button", { name: /Relief/ });
    expect(chip).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape and returns focus to the entry button", async () => {
    renderControl();
    const entry = screen.getByRole("button", { name: /Map style|Layers/ });
    await userEvent.click(entry);
    screen.getByRole("slider", { name: /dim/i }).focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "Satellite" })).toBeNull();
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(entry).toHaveFocus();
  });

  it("stays open through a press on the map behind it", async () => {
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    await userEvent.click(document.body);
    expect(screen.getByRole("button", { name: "Satellite" })).toBeInTheDocument();
  });

  it("leaves an Escape to the tooltip open above it", async () => {
    renderControl({ layers: [{ id: "relief", label: "Relief", hint: "Column height = average delay", on: false, onToggle: vi.fn(), icon: <span /> }] });
    await userEvent.click(screen.getByRole("button", { name: /Map style|Layers/ }));
    act(() => screen.getByRole("button", { name: /Relief/ }).focus());
    expect(screen.getByRole("tooltip")).toHaveTextContent("Column height = average delay");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.getByRole("button", { name: "Satellite" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "Satellite" })).toBeNull();
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
