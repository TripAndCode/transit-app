import { useTranslation } from "react-i18next";
import { defaultsFor, needsRoute, type CardTemplate } from "../../components/askCardTemplates";

type Props = {
  templates: CardTemplate[];
  onInstantSubmit: (tpl: CardTemplate) => void;
  onOpenChip: (tpl: CardTemplate) => void;
};

export function AskLandingCards({ templates, onInstantSubmit, onOpenChip }: Props) {
  const { t } = useTranslation();
  const instant = templates.filter((tpl) => !needsRoute(tpl));
  const pills = templates.filter(needsRoute);

  return (
    <div style={{ textAlign: "left" }}>
      {instant.length > 0 && (
        <>
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--text-tertiary)",
              marginBottom: 8,
            }}
          >
            {t("ask.landing.cards_title")}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 20 }}>
            {instant.map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                onClick={() => onInstantSubmit(tpl)}
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 8,
                  padding: "12px 14px",
                  textAlign: "left",
                  cursor: "pointer",
                  fontSize: 13,
                  color: "var(--accent)",
                  fontWeight: 600,
                }}
              >
                {tpl.emoji} {tpl.buildSummary(defaultsFor(tpl), t)}
              </button>
            ))}
          </div>
        </>
      )}
      {pills.length > 0 && (
        <>
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--text-tertiary)",
              marginBottom: 8,
            }}
          >
            {t("ask.landing.pills_title")}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
            {pills.map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                onClick={() => onOpenChip(tpl)}
                style={{
                  padding: "6px 12px",
                  background: "var(--bg-soft)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 20,
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                }}
              >
                {tpl.emoji} {tpl.buildSummary({}, t)}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
