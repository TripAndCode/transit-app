import { useTranslation } from "react-i18next";

type Props = {
  hour: number;
  onHourChange: (hour: number) => void;
  expectedDelayMin: number | null;
  playing: boolean;
  onTogglePlay: () => void;
};

export function MapHourScrubber({ hour, onHourChange, expectedDelayMin, playing, onTogglePlay }: Props) {
  const { t } = useTranslation();

  return (
    <div
      style={{
        position: "absolute",
        bottom: 20,
        left: "50%",
        transform: "translateX(-50%)",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-soft)",
        borderRadius: 10,
        padding: "10px 16px",
        display: "flex",
        alignItems: "center",
        gap: 12,
        width: "min(520px, 80vw)",
        boxShadow: "0 8px 28px rgba(0,0,0,0.12)",
      }}
    >
      <span style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
        {t("map.scrubber.hour_label", { hour: 6 })}
      </span>
      <input
        type="range"
        aria-label={t("map.scrubber.aria_label")}
        min={6}
        max={23}
        value={hour}
        onChange={(e) => onHourChange(Number(e.target.value))}
        style={{ flex: 1 }}
      />
      <span style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
        {t("map.scrubber.hour_label", { hour: 23 })}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
        {t("map.scrubber.hour_label", { hour })}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
        {expectedDelayMin != null
          ? t("map.scrubber.expected_delay", { min: expectedDelayMin.toFixed(1) })
          : t("map.scrubber.no_data")}
      </span>
      <button
        type="button"
        onClick={onTogglePlay}
        style={{
          background: "var(--accent)",
          color: "#fff",
          border: "none",
          borderRadius: 6,
          padding: "6px 14px",
          fontSize: 12,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        {playing ? t("map.scrubber.pause") : t("map.scrubber.play")}
      </button>
    </div>
  );
}
