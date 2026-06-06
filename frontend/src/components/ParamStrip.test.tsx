import { describe, it, expect, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { ParamStrip } from "./ParamStrip";
import { buildCardTemplates } from "./askCardTemplates";
import i18n from "../i18n";

const templates = buildCardTemplates();
const topDelay = templates.find((t) => t.id === "top_delay")!; // limit + service
const routeTrend = templates.find((t) => t.id === "route_trend")!; // route + granularity

function setup(
  props: Partial<Parameters<typeof ParamStrip>[0]> = {},
) {
  const onChange = vi.fn();
  const onSubmit = vi.fn();
  renderWithProviders(
    <ParamStrip
      template={topDelay}
      agencyId={1}
      values={{ k: 5, service_type: "all" }}
      onChange={onChange}
      onSubmit={onSubmit}
      busy={false}
      missing={[]}
      {...props}
    />,
  );
  return { onChange, onSubmit };
}

describe("ParamStrip", () => {
  it("renders the template title and a pill for each param", () => {
    setup();
    expect(
      screen.getByText(new RegExp(i18n.t(topDelay.title_key))),
    ).toBeInTheDocument();
    // Service segmented pill (a listbox trigger) and limit pill are present.
    expect(
      screen.getByRole("button", { name: /All/, expanded: false }),
    ).toBeInTheDocument();
  });

  it("submits via the Run button when nothing is missing", async () => {
    const user = userEvent.setup();
    const { onSubmit } = setup({ missing: [] });
    const run = screen.getByRole("button", { name: i18n.t("ask.dock.run") });
    expect(run).toBeEnabled();
    await user.click(run);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("disables Run and marks the field while a required param is missing", () => {
    const { onSubmit } = setup({
      template: routeTrend,
      values: { granularity: "week" },
      missing: ["route_code"],
    });
    const run = screen.getByRole("button", { name: i18n.t("ask.dock.run") });
    expect(run).toBeDisabled();
    // A required-marker (aria-labelled "*") is rendered for the missing field.
    expect(
      screen.getByLabelText(i18n.t("ask.dock.required_marker")),
    ).toBeInTheDocument();
    // It also cannot be submitted.
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("emits onChange with the selected value when a pill option is picked", async () => {
    const user = userEvent.setup();
    const { onChange } = setup();

    // Open the service SegmentedPill and choose Weekday.
    await user.click(screen.getByRole("button", { name: /All/ }));
    const listbox = screen.getByRole("listbox");
    await user.click(within(listbox).getByRole("option", { name: "Weekday" }));

    expect(onChange).toHaveBeenCalledWith("service_type", "weekday");
  });
});
