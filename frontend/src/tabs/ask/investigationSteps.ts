import type { ConvMessage } from "../../api/types";

export type InvestigationStep = {
  id: number;
  question: string;
  messages: ConvMessage[];
};

// Anonymous history can start with an assistant after retention truncation.
// Keep that result accessible without inventing a missing question.
export function investigationSteps(messages: ConvMessage[]): InvestigationStep[] {
  const steps: InvestigationStep[] = [];
  for (const message of messages) {
    if (message.role === "user" || steps.length === 0) {
      steps.push({
        id: message.message_id,
        question: message.role === "user" ? message.rendered_summary ?? "" : "",
        messages: [],
      });
    }
    steps[steps.length - 1].messages.push(message);
  }
  return steps;
}
