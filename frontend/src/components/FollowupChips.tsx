import { useTranslation } from "react-i18next";
import type { ConvMessage, FollowupChip } from "../api/types";
import { generateFollowups } from "./followupTemplates";

type Props = {
  message: ConvMessage;
  onPickFollowup: (chip: FollowupChip) => void;       // dispatch the follow-up tool/args
  onOpenBuilder: () => void;                           // "🛠 すべての条件を変えて再実行" — open builder pre-populated
  onBackToCatalog: () => void;                         // "＋ カタログに戻る" — restore empty-state catalog
};

export function FollowupChips({ message, onPickFollowup, onOpenBuilder, onBackToCatalog }: Props) {
  const { i18n, t } = useTranslation();
  if (message.role !== "assistant" || !message.tool || !message.args) return null;

  const firstRow = (message.result?.rows?.[0] as unknown[] | undefined) ?? null;
  const lang = (i18n.resolvedLanguage === "en" ? "en" : "ja") as "ja" | "en";
  const followups = generateFollowups(message.tool, message.args, firstRow, lang);

  if (followups.length === 0 && !onOpenBuilder && !onBackToCatalog) return null;

  return (
    <div style={{ marginTop: 8 }}>
      {followups.length > 0 && (
        <>
          <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>
            ▾ {t("ask.followups.heading", { defaultValue: "この結果から" })}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {followups.map((f) => (
              <button
                key={f.id}
                onClick={() => onPickFollowup(f)}
                style={{
                  padding: "4px 10px", borderRadius: 14, border: "1px solid rgba(0,0,0,.08)",
                  background: "rgba(0,0,0,.04)", cursor: "pointer", fontSize: 12,
                }}
              >
                {f.title}
              </button>
            ))}
          </div>
        </>
      )}
      <div style={{ display: "flex", gap: 12, fontSize: 12, opacity: 0.78 }}>
        <a onClick={onOpenBuilder} style={{ cursor: "pointer", textDecoration: "underline" }}>
          🛠 {t("ask.followups.open_builder", { defaultValue: "すべての条件を変えて再実行" })}
        </a>
        <a onClick={onBackToCatalog} style={{ cursor: "pointer", textDecoration: "underline" }}>
          ＋ {t("ask.followups.back_to_catalog", { defaultValue: "カタログに戻る" })}
        </a>
      </div>
    </div>
  );
}
