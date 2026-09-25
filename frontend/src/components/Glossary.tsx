import { Tooltip } from "./Tooltip";

type Props = {
  /** The technical term shown inline, kept exactly as-is (e.g. "GTFS") --
   *  this is for a term that must stay visible even on an operator-facing
   *  page, not a candidate for plain-language replacement. */
  term: string;
  /** Already-translated plain-language explanation, shown on hover/focus. */
  explanation: string;
};

/** Inline trigger that keeps a technical term visible in the UI while giving
 *  operators a plain-language explanation on demand, instead of either
 *  spelling the jargon out inline or hiding the distinction it names. */
export function Glossary({ term, explanation }: Props) {
  return (
    <Tooltip label={explanation}>
      <button type="button" className="glossary-term">
        {term}
      </button>
    </Tooltip>
  );
}
