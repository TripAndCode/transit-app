import type { StyleSpecification } from "maplibre-gl";

export type MapStyleId = "osm" | "pale" | "std" | "photo";

// GSI's pale (淡色) reads calmer under the map's data overlays than a busy
// full-color basemap; OSM and the other GSI styles remain available as
// options. Changing this only affects users with no stored choice.
export const DEFAULT_MAP_STYLE_ID: MapStyleId = "pale";

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

/** Every id the catalog defines, for validating an id that arrives from
 *  outside the app (a shared URL, a stored preference). Derived from the
 *  catalog so a new style cannot be added without becoming accepted. */
export const MAP_STYLE_IDS: readonly MapStyleId[] = MAP_STYLES.map((s) => s.id);

/** Build a MapLibre raster style for the given catalog id. English-label
 *  tiles are used only when `lang` starts with "en" AND the style defines
 *  `tilesEn` (only `std` does — GSI publishes a single English style). */
// Keyless glyph endpoint so symbol layers (the cluster-count labels) can render
// text — our raster basemaps carry no glyphs of their own. MapLibre's hosted
// Noto Sans set; swap for a self-hosted `/fonts/{fontstack}/{range}.pbf` if we
// ever want zero external font dependency.
const GLYPHS_URL = "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf";

export function buildStyle(id: MapStyleId, lang: string): StyleSpecification {
  const def = MAP_STYLES.find((s) => s.id === id) ?? MAP_STYLES[0];
  const tiles = lang.startsWith("en") && def.tilesEn ? def.tilesEn : def.tiles;
  return {
    version: 8,
    glyphs: GLYPHS_URL,
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

/** Slippy-map tile index (x, y) containing (lng, lat) at an integer zoom —
 *  the standard Web Mercator tile formula every {z}/{x}/{y} raster template
 *  in the catalog expects. */
function tileXY(lng: number, lat: number, zoom: number): { x: number; y: number } {
  const n = 2 ** zoom;
  const x = Math.floor(((lng + 180) / 360) * n);
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n);
  return { x: Math.min(Math.max(x, 0), n - 1), y: Math.min(Math.max(y, 0), n - 1) };
}

/** Wide view of Japan, used for a style's thumbnail before a live map view is
 *  available (before the map mounts, or in a render with no `mapRef`) —
 *  chosen so every catalog entry's tile request lands on real territory
 *  rather than open ocean. */
export const DEFAULT_THUMBNAIL_VIEW = { lng: 138.0, lat: 37.5, zoom: 5 };

/** Build a single tile URL from a catalog style's own template at the given
 *  center/zoom, for use as a basemap-switcher thumbnail. English-label tiles
 *  are picked the same way `buildStyle` picks them. Zoom is clamped to the
 *  style's own `maxzoom` so a thumbnail never requests an unpublished level. */
export function buildThumbnailUrl(
  id: MapStyleId,
  lang: string,
  view: { lng: number; lat: number; zoom: number } = DEFAULT_THUMBNAIL_VIEW,
): string {
  const def = MAP_STYLES.find((s) => s.id === id) ?? MAP_STYLES[0];
  const tiles = lang.startsWith("en") && def.tilesEn ? def.tilesEn : def.tiles;
  const z = Math.round(Math.min(Math.max(view.zoom, 0), def.maxzoom));
  const { x, y } = tileXY(view.lng, view.lat, z);
  return tiles[0].replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y));
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

/** Slider ceiling for the basemap dim (`useBasemapDim`'s `dimAmount`): a
 *  calm map keeps some basemap saturation visible even at maximum, so the
 *  control never offers a fully greyed-out extreme. */
export const MAX_DIM_AMOUNT = 0.6;

// The pre-slider design always dimmed at full strength; splitting the
// difference between "off" and that ceiling reads as calm rather than
// visually flat, and is used only when no user choice is stored yet.
const DEFAULT_DIM_AMOUNT = MAX_DIM_AMOUNT / 2;

const DIM_PREF_KEY = "transit.mapDim";

/** Read the persisted basemap-dim amount, clamped to [0, MAX_DIM_AMOUNT]. */
export function readMapDimPref(): number {
  try {
    const v = localStorage.getItem(DIM_PREF_KEY);
    const n = v == null ? NaN : Number(v);
    if (Number.isFinite(n)) return Math.min(Math.max(n, 0), MAX_DIM_AMOUNT);
  } catch {
    /* localStorage unavailable — fall through */
  }
  return DEFAULT_DIM_AMOUNT;
}

/** Persist the chosen basemap-dim amount, clamped to [0, MAX_DIM_AMOUNT].
 *  No-ops if localStorage is unavailable. */
export function writeMapDimPref(amount: number): void {
  try {
    localStorage.setItem(DIM_PREF_KEY, String(Math.min(Math.max(amount, 0), MAX_DIM_AMOUNT)));
  } catch {
    /* ignore */
  }
}
