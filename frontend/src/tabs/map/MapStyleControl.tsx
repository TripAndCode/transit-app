import { useState } from "react";
import type { TFunction } from "i18next";
import { MAP_STYLES, type MapStyleId } from "../../styles/mapStyle";

// Representative thumbnail tile (Aomori, z11) per style — decorative.
const THUMB: Record<MapStyleId, string> = {
  osm: "https://a.tile.openstreetmap.org/11/1824/769.png",
  pale: "https://cyberjapandata.gsi.go.jp/xyz/pale/11/1824/769.png",
  std: "https://cyberjapandata.gsi.go.jp/xyz/std/11/1824/769.png",
  photo: "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/11/1824/769.jpg",
};

const THUMB_PX = 60;

// Solid white + a real drop shadow + hairline border so the control reads on
// ANY basemap — light (淡色/OSM), busy (標準), and dark imagery (航空写真).
// (The previous translucent --bg-surface chip blended into light basemaps.)
const PILL: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid rgba(0,0,0,0.14)",
  borderRadius: 14,
  boxShadow: "0 3px 14px rgba(0,0,0,0.28)",
};

function Tile({
  styleId,
  label,
  active,
  onClick,
}: {
  styleId: MapStyleId;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 5,
        padding: 0,
        background: "transparent",
        border: "none",
        cursor: "pointer",
        width: THUMB_PX,
      }}
    >
      <img
        src={THUMB[styleId]}
        alt=""
        width={THUMB_PX}
        height={THUMB_PX}
        style={{
          borderRadius: 12,
          objectFit: "cover",
          outline: active ? "3px solid var(--accent)" : "1px solid rgba(0,0,0,0.12)",
          outlineOffset: active ? -1 : 0,
        }}
      />
      <span
        style={{
          fontSize: 12,
          lineHeight: 1.1,
          fontWeight: active ? 700 : 500,
          color: active ? "var(--chip-accent)" : "var(--chip-text-secondary)",
          textAlign: "center",
        }}
      >
        {label}
      </span>
    </button>
  );
}

export function MapStyleControl({
  value,
  onChange,
  t,
}: {
  value: MapStyleId;
  onChange: (id: MapStyleId) => void;
  t: TFunction;
}) {
  const [open, setOpen] = useState(false);
  const current = MAP_STYLES.find((s) => s.id === value) ?? MAP_STYLES[0];

  return (
    <div
      style={{
        position: "absolute",
        left: 12,
        bottom: 28,
        zIndex: 2,
        display: "flex",
        alignItems: "flex-end",
        gap: 8,
      }}
    >
      {/* Entry button: current-style thumbnail + "Layers" — Google-style affordance. */}
      <button
        type="button"
        aria-label={t("map.style.label")}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{
          ...PILL,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 4,
          padding: "8px 10px",
          cursor: "pointer",
        }}
      >
        <img
          src={THUMB[current.id]}
          alt=""
          width={THUMB_PX}
          height={THUMB_PX}
          style={{ borderRadius: 12, objectFit: "cover", outline: "1px solid rgba(0,0,0,0.12)" }}
        />
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--chip-text-primary)" }}>
          {t("map.style.layers")}
        </span>
      </button>

      {open && (
        <div style={{ ...PILL, display: "flex", gap: 14, padding: "10px 14px", maxWidth: "70vw", overflowX: "auto" }}>
          {MAP_STYLES.map((s) => (
            <Tile
              key={s.id}
              styleId={s.id}
              label={t(s.labelKey)}
              active={s.id === value}
              onClick={() => {
                onChange(s.id);
                setOpen(false);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
