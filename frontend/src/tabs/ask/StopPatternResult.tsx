import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { StopEvidenceChart } from "./StopEvidenceChart";
import type { StopEvidence, StopFocus } from "./stopEvidence";

export function StopPatternResult({ messageId, points, onFocus, message }: {
  messageId: number; points: StopEvidence[]; onFocus: (focus: StopFocus | null) => void;
  message?: ConvMessage;
}) {
  const { t } = useTranslation();
  const [chosen, setChosen] = useState<string | null>(null);
  const patterns = [...new Map(points.map((point) => [point.patternId, point.patternName])).entries()];
  const patternId = patterns.some(([key]) => key === chosen) ? chosen : patterns[0]?.[0];
  const visible = points.filter((point) => point.patternId === patternId).sort((a, b) => a.sequence - b.sequence);
  return <>
    <label className="ask-pattern-picker">
      {t("ask.evidence.pattern")}
      <select value={patternId ?? ""} onChange={(event) => { setChosen(event.target.value); onFocus(null); }}>
        {patterns.map(([key, name], index) => <option key={key} value={key}>{index + 1}. {name}</option>)}
      </select>
    </label>
    <StopEvidenceChart key={`${messageId}:${patternId}`} messageId={messageId} points={visible} onFocus={onFocus} message={message} complete />
  </>;
}
