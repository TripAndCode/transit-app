import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { defaultsFor, needsRoute, type CardTemplate } from "../../components/askCardTemplates";

type Props = {
  templates: CardTemplate[];
  onInstantSubmit: (tpl: CardTemplate) => void;
  onOpenChip: (tpl: CardTemplate) => void;
  /** True while a dispatch is in flight — disables the buttons so a fast
   *  double-click on an instant card can't fire two conversation/message
   *  creates before the first one settles. */
  busy?: boolean;
};

export function AskLandingCards({ templates, onInstantSubmit, onOpenChip, busy = false }: Props) {
  const { t } = useTranslation();
  const instant = templates.filter((tpl) => !needsRoute(tpl));
  const pills = templates.filter(needsRoute);

  return (
    <div style={{ textAlign: "left" }}>
      <div style={{ textAlign: "center", marginBottom: 24 }}>
        <div
          style={{
            width: 40,
            height: 40,
            background: "var(--accent-soft)",
            border: "1px solid var(--accent)",
            borderRadius: 10,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 8,
          }}
        >
          <Search size={20} strokeWidth={1.8} color="var(--accent)" aria-hidden="true" />
        </div>
        <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.01em" }}>
          {t("ask.landing.header_title")}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 3 }}>
          {t("ask.landing.header_subtitle")}
        </div>
      </div>
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
                disabled={busy}
                aria-disabled={busy}
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 8,
                  padding: "12px 14px",
                  textAlign: "left",
                  cursor: busy ? "not-allowed" : "pointer",
                  opacity: busy ? 0.6 : 1,
                }}
              >
                <div style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600 }}>
                  {tpl.emoji} {tpl.buildSummary(defaultsFor(tpl), t)}
                </div>
                {tpl.example_answer_key && (
                  <div
                    style={{
                      fontSize: 11.5,
                      color: "var(--text-tertiary)",
                      marginTop: 6,
                      paddingLeft: 20,
                      lineHeight: 1.55,
                    }}
                  >
                    {t(tpl.example_answer_key)}
                  </div>
                )}
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
                disabled={busy}
                aria-disabled={busy}
                style={{
                  padding: "6px 12px",
                  background: "var(--bg-soft)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 20,
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  cursor: busy ? "not-allowed" : "pointer",
                  opacity: busy ? 0.6 : 1,
                }}
              >
                {tpl.emoji} {t(tpl.title_key)}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
