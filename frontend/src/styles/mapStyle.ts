/// <reference types="vite/client" />
import type { StyleSpecification } from "maplibre-gl";

const OSM_RASTER: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [
        "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "osm", type: "raster", source: "osm" },
  ],
};

export function getMapStyle(): string | StyleSpecification {
  const url = import.meta.env.VITE_MAP_STYLE_URL;
  if (typeof url === "string" && url.length > 0) return url;
  return OSM_RASTER;
}
