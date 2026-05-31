/** Predefined follow-up chips shown under the last result bubble. */

export type FollowupChip = { id: string; label_key: string; prompt_key: string };

export const FOLLOWUP_CHIPS: FollowupChip[] = [
  {
    id: "why",
    label_key: "ask.followup_chips.chips.why.label",
    prompt_key: "ask.followup_chips.chips.why.prompt",
  },
  {
    id: "reliability",
    label_key: "ask.followup_chips.chips.reliability.label",
    prompt_key: "ask.followup_chips.chips.reliability.prompt",
  },
  {
    id: "slice",
    label_key: "ask.followup_chips.chips.slice.label",
    prompt_key: "ask.followup_chips.chips.slice.prompt",
  },
  {
    id: "summarize",
    label_key: "ask.followup_chips.chips.summarize.label",
    prompt_key: "ask.followup_chips.chips.summarize.prompt",
  },
  {
    id: "next",
    label_key: "ask.followup_chips.chips.next.label",
    prompt_key: "ask.followup_chips.chips.next.prompt",
  },
];
