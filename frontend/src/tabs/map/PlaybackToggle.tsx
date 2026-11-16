import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";

/** Switches the map between the trips reporting now and a playback of the
 *  day. It floats over the map apart from the zoom and fit controls because
 *  it replaces what the map shows rather than reframing it. */
export function PlaybackToggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="ops-dock">
      <button type="button" className="btn btn--ghost btn--sm ops-dock__playback" aria-pressed={active} onClick={onToggle}>
        <Clapperboard size={14} aria-hidden="true" />
        <span className="ops-dock__playback-label">
          {t(active ? "operations.playback.toggle_off" : "operations.playback.toggle_on")}
        </span>
      </button>
    </div>
  );
}
