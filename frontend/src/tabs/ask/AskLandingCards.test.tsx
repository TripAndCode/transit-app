import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AskLandingCards } from "./AskLandingCards";
import { buildCardTemplates, needsRoute } from "../../components/askCardTemplates";

const templates = buildCardTemplates();

// buildSummary() output can contain regex metacharacters (e.g. the literal
// parens in "Top 5 routes (All)"), so escape before feeding it to RegExp —
// otherwise `(All)` is parsed as a capture group and silently stops matching
// the literal parens in the rendered text.
function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function setup(overrides: { busy?: boolean } = {}) {
  const onInstantSubmit = vi.fn();
  const onOpenChip = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <AskLandingCards
        templates={templates}
        onInstantSubmit={onInstantSubmit}
        onOpenChip={onOpenChip}
        {...overrides}
      />
    </I18nextProvider>,
  );
  return { onInstantSubmit, onOpenChip };
}

describe("AskLandingCards", () => {
  it("renders one instant card per no-route template", () => {
    setup();
    // top_delay and ontime_rank have no required route param.
    expect(screen.getByText(i18n.t("ask.landing.cards_title"))).toBeInTheDocument();
    const topDelayTpl = templates.find((t) => t.id === "top_delay")!;
    expect(
      screen.getByText(new RegExp(escapeRegExp(topDelayTpl.buildSummary({ k: 5, service_type: "all" }, i18n.t.bind(i18n))))),
    ).toBeInTheDocument();
  });

  it("renders one pill per route-required template, each with a distinct label", () => {
    setup();
    expect(screen.getByText(i18n.t("ask.landing.pills_title"))).toBeInTheDocument();
    // Pills show the template's title, not buildSummary's "select a route"
    // placeholder -- that placeholder is identical across every
    // route-required template and can't disambiguate them by text alone.
    for (const tpl of templates.filter(needsRoute)) {
      expect(screen.getByText(new RegExp(escapeRegExp(i18n.t(tpl.title_key))))).toBeInTheDocument();
    }
  });

  it("calls onInstantSubmit with the right template when an instant card is clicked", async () => {
    const user = userEvent.setup();
    const { onInstantSubmit } = setup();
    const topDelayTpl = templates.find((t) => t.id === "top_delay")!;
    const label = topDelayTpl.buildSummary({ k: 5, service_type: "all" }, i18n.t.bind(i18n));
    await user.click(screen.getByText(new RegExp(escapeRegExp(label))));
    expect(onInstantSubmit).toHaveBeenCalledTimes(1);
    expect(onInstantSubmit.mock.calls[0][0].id).toBe("top_delay");
  });

  it("calls onOpenChip with the right template when a pill is clicked", async () => {
    const user = userEvent.setup();
    const { onOpenChip } = setup();
    const routeTrendTpl = templates.find((t) => t.id === "route_trend")!;
    const label = i18n.t(routeTrendTpl.title_key);
    await user.click(screen.getByText(new RegExp(escapeRegExp(label))));
    expect(onOpenChip).toHaveBeenCalledTimes(1);
    expect(onOpenChip.mock.calls[0][0].id).toBe("route_trend");
  });

  it("disables cards and pills and does not dispatch while busy", async () => {
    const user = userEvent.setup();
    const { onInstantSubmit, onOpenChip } = setup({ busy: true });
    const topDelayTpl = templates.find((t) => t.id === "top_delay")!;
    const cardLabel = topDelayTpl.buildSummary({ k: 5, service_type: "all" }, i18n.t.bind(i18n));
    const card = screen.getByText(new RegExp(escapeRegExp(cardLabel))).closest("button")!;
    expect(card).toBeDisabled();
    await user.click(card);
    expect(onInstantSubmit).not.toHaveBeenCalled();

    const routeTrendTpl = templates.find((t) => t.id === "route_trend")!;
    const pillLabel = i18n.t(routeTrendTpl.title_key);
    const pill = screen.getByText(new RegExp(escapeRegExp(pillLabel))).closest("button")!;
    expect(pill).toBeDisabled();
    await user.click(pill);
    expect(onOpenChip).not.toHaveBeenCalled();
  });

  it("renders the landing header with icon, title, and subtitle", () => {
    setup();
    expect(screen.getByText(i18n.t("ask.landing.header_title"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("ask.landing.header_subtitle"))).toBeInTheDocument();
    // The Search icon is decorative (aria-hidden), so it's unreachable via
    // getByRole — assert it rendered directly.
    expect(document.querySelector("svg")).toBeInTheDocument();
  });

  it("shows an example-answer line under instant cards that have one, none for pills", () => {
    setup();
    expect(screen.getByText(i18n.t("ask.card.top_delay.example_answer"))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("ask.card.ontime_rank.example_answer"))).toBeInTheDocument();
    // Pills render only emoji + summary text (no example-answer line ever) —
    // assert none of their buttons carries an extra child element.
    const pillsWrap = screen.getByText(i18n.t("ask.landing.pills_title")).nextElementSibling!;
    const pillButtons = pillsWrap.querySelectorAll("button");
    expect(pillButtons.length).toBeGreaterThan(0);
    pillButtons.forEach((btn) => expect(btn.children).toHaveLength(0));
  });
});
