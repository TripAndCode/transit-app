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
        left: 10,
        bottom: 28,
        zIndex: 2,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-soft)",
        borderRadius: 8,
        boxShadow: "0 2px 8px rgba(0,0,0,0.10)",
        overflow: "hidden",
        fontSize: 12,
      }}
    >
      <button
        type="button"
        aria-label={t("map.style.label")}
        onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: "transparent", border: "none", cursor: "pointer", width: "100%" }}
      >
        <img src={THUMB[current.id]} alt="" width={28} height={28} style={{ borderRadius: 4, objectFit: "cover" }} />
        <span style={{ fontWeight: 600 }}>{t(current.labelKey)}</span>
      </button>
      {open && (
        <div style={{ borderTop: "1px solid var(--border-subtle)" }}>
          {MAP_STYLES.map((s) => (
            <button
              key={s.id}
              type="button"
              aria-pressed={s.id === value}
              onClick={() => {
                onChange(s.id);
                setOpen(false);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 10px",
                width: "100%",
                background: s.id === value ? "var(--accent-soft)" : "transparent",
                color: s.id === value ? "var(--accent)" : "var(--text-primary)",
                border: "none",
                cursor: "pointer",
                textAlign: "left",
              }}
            >
              <img src={THUMB[s.id]} alt="" width={28} height={28} style={{ borderRadius: 4, objectFit: "cover" }} />
              <span>{t(s.labelKey)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
