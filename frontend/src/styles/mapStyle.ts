import type { StyleSpecification } from "maplibre-gl";

export type MapStyleId = "osm" | "pale" | "std" | "photo";

// The original pre-feature basemap (OSM) stays the default; the GSI styles are
// additional options. Changing this only affects users with no stored choice.
export const DEFAULT_MAP_STYLE_ID: MapStyleId = "osm";

const GSI = "https://cyberjapandata.gsi.go.jp/xyz";
const GSI_ATTRIBUTION = "© 国土地理院"; // i18n-ignore: legally-required GSI tile attribution (official source name, not UI chrome)
const OSM_ATTRIBUTION = "© OpenStreetMap contributors";

type MapStyleDef = {
  id: MapStyleId;
  labelKey: string;
  tiles: string[];
  tilesEn?: string[];
  attribution: string;
  maxzoom: number;
};

export const MAP_STYLES: MapStyleDef[] = [
  {
    id: "osm",
    labelKey: "map.style.osm",
    tiles: [
      "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
      "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
      "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
    ],
    attribution: OSM_ATTRIBUTION,
    maxzoom: 19,
  },
  { id: "pale", labelKey: "map.style.pale", tiles: [`${GSI}/pale/{z}/{x}/{y}.png`], attribution: GSI_ATTRIBUTION, maxzoom: 18 },
  {
    id: "std",
    labelKey: "map.style.std",
    tiles: [`${GSI}/std/{z}/{x}/{y}.png`],
    tilesEn: [`${GSI}/english/{z}/{x}/{y}.png`],
    attribution: GSI_ATTRIBUTION,
    maxzoom: 18,
  },
  {
    id: "photo",
    labelKey: "map.style.photo",
    tiles: [`${GSI}/seamlessphoto/{z}/{x}/{y}.jpg`],
    attribution: GSI_ATTRIBUTION,
    maxzoom: 18,
  },
];

/** Build a MapLibre raster style for the given catalog id. English-label
 *  tiles are used only when `lang` starts with "en" AND the style defines
 *  `tilesEn` (only `std` does — GSI publishes a single English style). */
export function buildStyle(id: MapStyleId, lang: string): StyleSpecification {
  const def = MAP_STYLES.find((s) => s.id === id) ?? MAP_STYLES[0];
  const tiles = lang.startsWith("en") && def.tilesEn ? def.tilesEn : def.tiles;
  return {
    version: 8,
    sources: {
      basemap: { type: "raster", tiles, tileSize: 256, maxzoom: def.maxzoom, attribution: def.attribution },
    },
    layers: [{ id: "basemap", type: "raster", source: "basemap" }],
  };
}

/** Env escape hatch: if VITE_MAP_STYLE_URL is set it overrides the catalog
 *  (returns the URL string); otherwise null and the catalog drives. */
export function getMapStyleOverride(): string | null {
  const url = import.meta.env.VITE_MAP_STYLE_URL;
  return typeof url === "string" && url.length > 0 ? url : null;
}

const PREF_KEY = "transit.mapStyle";

/** Read the persisted style id, validated against the catalog. */
export function readMapStylePref(): MapStyleId {
  try {
    const v = localStorage.getItem(PREF_KEY);
    if (v && MAP_STYLES.some((s) => s.id === v)) return v as MapStyleId;
  } catch {
    /* localStorage unavailable — fall through */
  }
  return DEFAULT_MAP_STYLE_ID;
}

/** Persist the chosen style id. No-ops if localStorage is unavailable. */
export function writeMapStylePref(id: MapStyleId): void {
  try {
    localStorage.setItem(PREF_KEY, id);
  } catch {
    /* ignore */
  }
}
