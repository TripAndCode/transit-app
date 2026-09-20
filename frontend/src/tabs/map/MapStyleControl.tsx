import { useEffect, useState } from "react";
import type { TFunction } from "i18next";
import type { Map as MLMap } from "maplibre-gl";
import { buildThumbnailUrl, DEFAULT_THUMBNAIL_VIEW, MAP_STYLES, MAX_DIM_AMOUNT, type MapStyleId } from "../../styles/mapStyle";

const THUMB_PX = 44;

function Tile({
  label,
  active,
  thumbSrc,
  onClick,
}: {
  label: string;
  active: boolean;
  thumbSrc: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className="ops-style-control__tile" aria-pressed={active} onClick={onClick}>
      <img
        src={thumbSrc}
        alt=""
        width={THUMB_PX}
        height={THUMB_PX}
        className={active ? "ops-style-control__thumb ops-style-control__thumb--active" : "ops-style-control__thumb"}
      />
      <span className={active ? "ops-style-control__label ops-style-control__label--active" : "ops-style-control__label"}>
        {label}
      </span>
    </button>
  );
}

export function MapStyleControl({
  value,
  onChange,
  dimAmount,
  onDimChange,
  mapRef,
  lang = "ja",
  t,
}: {
  value: MapStyleId;
  onChange: (id: MapStyleId) => void;
  dimAmount: number;
  onDimChange: (amount: number) => void;
  mapRef?: React.MutableRefObject<MLMap | null>;
  /** UI language, for picking a style's English tile template (only `std` has
   *  one) — matches the same `lang.startsWith("en")` rule `buildStyle` uses. */
  lang?: string;
  t: TFunction;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(DEFAULT_THUMBNAIL_VIEW);
  const current = MAP_STYLES.find((s) => s.id === value) ?? MAP_STYLES[0];

  // Reads the map's current view (an imperative MapLibre instance, not React
  // state) so a thumbnail reflects where the operator is actually looking
  // rather than a fixed reference tile. Re-synced on open rather than on
  // every pan/zoom, so the three non-current style tiles are only ever
  // fetched — not just rendered — while the switcher is actually expanded.
  useEffect(() => {
    const map = mapRef?.current;
    if (!map) return;
    const center = map.getCenter();
    setView({ lng: center.lng, lat: center.lat, zoom: map.getZoom() });
  }, [open, mapRef]);

  const dimPercent = Math.round(dimAmount * 100);
  const maxDimPercent = Math.round(MAX_DIM_AMOUNT * 100);

  return (
    <div className="ops-style-control">
      {/* Entry button: current-style thumbnail + "Layers" — Google-style affordance. */}
      <button
        type="button"
        className="ops-style-control__entry map-chrome"
        aria-label={t("map.style.label")}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <img
          src={buildThumbnailUrl(current.id, lang, view)}
          alt=""
          width={THUMB_PX}
          height={THUMB_PX}
          className="ops-style-control__thumb"
        />
        <span className="ops-style-control__entry-label">{t("map.style.layers")}</span>
      </button>

      {open && (
        <div className="ops-style-control__panel map-chrome">
          <div className="ops-style-control__tiles">
            {MAP_STYLES.map((s) => (
              <Tile
                key={s.id}
                label={t(s.labelKey)}
                active={s.id === value}
                thumbSrc={buildThumbnailUrl(s.id, lang, view)}
                onClick={() => {
                  onChange(s.id);
                  setOpen(false);
                }}
              />
            ))}
          </div>
          <div className="ops-style-control__dim">
            <label htmlFor="ops-map-dim">{t("map.style.dim_label")}</label>
            <input
              id="ops-map-dim"
              type="range"
              min={0}
              max={maxDimPercent}
              step={5}
              value={dimPercent}
              onChange={(e) => onDimChange(Number(e.target.value) / 100)}
            />
            <output htmlFor="ops-map-dim" className="num">{dimPercent}%</output>
          </div>
        </div>
      )}
    </div>
  );
}
