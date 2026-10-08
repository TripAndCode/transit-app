import type { Map as MLMap } from "maplibre-gl";

/** Writes a paint builder's whole output onto a layer that already exists.
 *
 *  Paint builders resolve theme tokens to concrete colours, because MapLibre
 *  cannot read `var()`. A layer hook whose source already exists takes its
 *  `setData` branch instead of re-adding the layer, so every resolved colour
 *  has to be written again there or it stays at the theme the layer was
 *  created under. Writing the builder's full output, not a hand-picked list
 *  of colour properties, keeps a property added to a builder from going
 *  stale the same way; MapLibre skips a write whose value is unchanged. */
export function repaintLayer(map: MLMap, layerId: string, paint: object): void {
  for (const [property, value] of Object.entries(paint)) map.setPaintProperty(layerId, property, value);
}
