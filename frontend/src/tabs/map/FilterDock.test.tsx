import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { FilterDock } from "./FilterDock";
import * as hooks from "../../api/hooks";

const ROUTES = [
  { route_id: "r1", route_code: "101", route_short_name: "101", route_long_name: "青森駅前", agency_id: 1 },
  { route_id: "r2", route_code: "102", route_short_name: "102", route_long_name: "青森駅前", agency_id: 1 },
];

const APPLY = /適用|apply/i;
const PENDING = /変更が未適用|not applied yet/i;

function renderDock(applied: string[] = []) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: ROUTES,
    isPending: false,
    error: null,
  } as unknown as ReturnType<typeof hooks.useRoutes>);
  const onApply = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <FilterDock agencyId={1} applied={applied} onApply={onApply} />
    </QueryClientProvider>,
  );
  return { onApply };
}

describe("FilterDock", () => {
  it("does not commit a selection until apply is pressed", async () => {
    const { onApply } = renderDock();
    const [line] = screen.getAllByRole("combobox");

    await userEvent.selectOptions(line, "青森駅前");
    expect(onApply).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: APPLY }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply.mock.calls[0][0]).toEqual(expect.arrayContaining(["101", "102"]));
  });

  it("offers no apply button at all until something actually changes", async () => {
    // The resting state is the common one (every route), so an always-present
    // button would be disabled almost all the time. Absent, not disabled.
    renderDock();
    expect(screen.queryByRole("button", { name: APPLY })).toBeNull();

    const [line] = screen.getAllByRole("combobox");
    await userEvent.selectOptions(line, "青森駅前");
    expect(screen.getByRole("button", { name: APPLY })).toBeTruthy();
  });

  it("announces the pending state for a screen reader, then stops once applied", async () => {
    // The button's appearance is the sighted signal; this status text is the
    // same fact for a reader that can't see the dock's edge change.
    renderDock();
    expect(screen.queryByText(PENDING)).toBeNull();

    const [line] = screen.getAllByRole("combobox");
    await userEvent.selectOptions(line, "青森駅前");
    expect(screen.getByRole("status")).toHaveTextContent(PENDING);

    await userEvent.click(screen.getByRole("button", { name: APPLY }));
    expect(screen.queryByText(PENDING)).toBeNull();
  });

  it("treats a selection that round-trips back to the applied one as not pending", async () => {
    const { onApply } = renderDock(["101", "102"]);
    const [line] = screen.getAllByRole("combobox");

    // Away and back: PatternFilters rebuilds a whole group's code list, so the
    // returning value can differ in order from `applied` while meaning the
    // same thing. Order must not read as a pending change.
    await userEvent.selectOptions(line, "");
    expect(screen.getByRole("button", { name: APPLY })).toBeTruthy();
    await userEvent.selectOptions(line, "青森駅前");

    expect(screen.queryByRole("button", { name: APPLY })).toBeNull();
    expect(screen.queryByText(PENDING)).toBeNull();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("commits on form submit, which is how Enter reaches it in a browser", async () => {
    // Asserted at the submit event, not by typing Enter: jsdom implements no
    // implicit form submission, so an Enter keystroke here would prove
    // nothing either way. Being a real <form> with a submit handler is what
    // makes the keyboard path work in a browser; that it applies on submit is
    // the part this component actually owns.
    const { onApply } = renderDock();
    const [line] = screen.getAllByRole("combobox");

    await userEvent.selectOptions(line, "青森駅前");
    fireEvent.submit(screen.getByRole("button", { name: APPLY }).closest("form")!);

    expect(onApply).toHaveBeenCalledTimes(1);
  });
});
