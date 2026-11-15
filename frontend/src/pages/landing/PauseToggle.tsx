import { useTranslation } from "react-i18next";

/** The page's one motion control (WCAG 2.2.2): toggles `.is-paused` on the
 *  landing root, which freezes every looping animation via
 *  `.landing-shell.is-paused *, ::before, ::after { animation-play-state:
 *  paused }`. The accessible name stays the same whether paused or playing --
 *  only `aria-pressed` and the icon change -- so a screen-reader user always
 *  hears "Pause animations", never a changing verb. */
export function PauseToggle({ paused, onToggle }: { paused: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" className="landing-pause" aria-pressed={paused} onClick={onToggle}>
      {paused ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M8 5v14l11-7z" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M7 5h4v14H7zM13 5h4v14h-4z" />
        </svg>
      )}
      <span>{t("landing.pause_toggle.label")}</span>
    </button>
  );
}
