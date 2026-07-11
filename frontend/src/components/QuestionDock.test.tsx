import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { QuestionDock } from "./QuestionDock";
import { buildCardTemplates, defaultsFor, type CardTemplate } from "./askCardTemplates";
import i18n from "../i18n";

const templates = buildCardTemplates();

/** Mimics AskTab's own composing-state ownership, so these tests exercise
 *  QuestionDock exactly as it's used in production (a controlled component),
 *  not a synthetic subset of props. */
function Harness({
  onSubmit,
  busy = false,
}: {
  onSubmit: (payload: { tool: string; args: Record<string, unknown>; user_summary: string }) => void | Promise<void>;
  busy?: boolean;
}) {
  const [composingId, setComposingId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});

  function handleChipTap(tpl: CardTemplate) {
    if (busy) return;
    if (composingId === tpl.id) {
      setComposingId(null);
      setValues({});
      return;
    }
    setComposingId(tpl.id);
    setValues(defaultsFor(tpl));
  }

  return (
    <QuestionDock
      agencyId={1}
      busy={busy}
      onSubmit={onSubmit}
      composingId={composingId}
      values={values}
      onChipTap={handleChipTap}
      onValueChange={(name, next) => setValues((prev) => ({ ...prev, [name]: next }))}
      onRunComplete={() => {
        setComposingId(null);
        setValues({});
      }}
    />
  );
}

function setup(overrides: { busy?: boolean } = {}) {
  const onSubmit = vi.fn();
  renderWithProviders(<Harness onSubmit={onSubmit} {...overrides} />);
  return { onSubmit };
}

describe("QuestionDock", () => {
  it("renders one chip per template inside the question-picker toolbar", () => {
    setup();
    const toolbar = screen.getByRole("toolbar", {
      name: i18n.t("ask.dock.chip_strip_aria"),
    });
    const chips = within(toolbar).getAllByRole("button");
    expect(chips).toHaveLength(templates.length);

    // Each template title is present on a chip.
    for (const tpl of templates) {
      expect(
        within(toolbar).getByRole("button", {
          name: new RegExp(i18n.t(tpl.title_key)),
        }),
      ).toBeInTheDocument();
    }
  });

  it("shows the idle hint and no ParamStrip until a chip is tapped", () => {
    setup();
    expect(screen.getByText(i18n.t("ask.dock.chip_strip_idle_hint"))).toBeInTheDocument();
    // The Run button only exists once ParamStrip mounts.
    expect(
      screen.queryByRole("button", { name: i18n.t("ask.dock.run") }),
    ).not.toBeInTheDocument();
  });

  it("raises the ParamStrip (with Run button) when a chip is tapped", async () => {
    const user = userEvent.setup();
    setup();
    const topDelay = templates.find((t) => t.id === "top_delay")!;

    await user.click(
      screen.getByRole("button", { name: new RegExp(i18n.t(topDelay.title_key)) }),
    );

    // ParamStrip's Run button now exists, idle hint is gone, chip is pressed.
    expect(screen.getByRole("button", { name: i18n.t("ask.dock.run") })).toBeInTheDocument();
    expect(
      screen.queryByText(i18n.t("ask.dock.chip_strip_idle_hint")),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: new RegExp(i18n.t(topDelay.title_key)) }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("emits the correct tool and merged args on 実行 (Run)", async () => {
    const user = userEvent.setup();
    const { onSubmit } = setup();
    const topDelay = templates.find((t) => t.id === "top_delay")!;

    await user.click(
      screen.getByRole("button", { name: new RegExp(i18n.t(topDelay.title_key)) }),
    );
    await user.click(screen.getByRole("button", { name: i18n.t("ask.dock.run") }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.tool).toBe("top_n");
    // fixed_args (metric) merged with default values (k, service_type).
    expect(payload.args).toMatchObject({
      metric: "avg_delay",
      k: 5,
      service_type: "all",
    });
    expect(typeof payload.user_summary).toBe("string");
    expect(payload.user_summary.length).toBeGreaterThan(0);
  });

  it("coerces the best_first metric string to a boolean in args", async () => {
    const user = userEvent.setup();
    const { onSubmit } = setup();
    const ontime = templates.find((t) => t.id === "ontime_rank")!;

    await user.click(
      screen.getByRole("button", { name: new RegExp(i18n.t(ontime.title_key)) }),
    );
    await user.click(screen.getByRole("button", { name: i18n.t("ask.dock.run") }));

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.tool).toBe("on_time");
    // default best_first is "false" (string) → coerced to boolean false.
    expect(payload.args.best_first).toBe(false);
  });

  it("collapses the ParamStrip when the active chip is tapped again", async () => {
    const user = userEvent.setup();
    setup();
    const topDelay = templates.find((t) => t.id === "top_delay")!;
    const chip = () =>
      screen.getByRole("button", { name: new RegExp(i18n.t(topDelay.title_key)) });

    await user.click(chip());
    expect(screen.getByRole("button", { name: i18n.t("ask.dock.run") })).toBeInTheDocument();

    await user.click(chip());
    expect(
      screen.queryByRole("button", { name: i18n.t("ask.dock.run") }),
    ).not.toBeInTheDocument();
  });
});
