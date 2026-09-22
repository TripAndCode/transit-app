import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { DataTable, type DataTableColumn } from "./DataTable";

type Row = { id: string; name: string };

const ROWS: Row[] = [
  { id: "a", name: "Hokuriku" },
  { id: "b", name: "Kaga" },
  { id: "c", name: "Noto" },
];

const COLUMNS: DataTableColumn<Row>[] = [{ key: "name", header: "Agency", render: (r) => r.name }];

function Harness(props: {
  onOpen?: (row: Row) => void;
  selectable?: boolean;
  savedViews?: { id: string; label: string; count?: number }[];
}) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [params] = useSearchParams();
  return (
    <>
      <span data-testid="selected">{[...selected].sort().join(",")}</span>
      <span data-testid="view">{params.get("view") ?? ""}</span>
      <DataTable
        caption="Agencies"
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        selectable={props.selectable}
        selectedIds={selected}
        onSelectionChange={setSelected}
        onOpen={props.onOpen}
        savedViews={props.savedViews}
      />
    </>
  );
}

function wrap(ui: React.ReactElement, initialEntries = ["/admin/agencies"]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </I18nextProvider>,
  );
}

describe("DataTable", () => {
  it("renders one focusable row per record under an accessible name", () => {
    wrap(<Harness />);
    expect(screen.getByRole("table", { name: "Agencies" })).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    for (const row of rows) expect(row).toHaveAttribute("tabindex", "0");
  });

  it("moves row focus with j and k without wrapping past the ends", async () => {
    const user = userEvent.setup();
    wrap(<Harness />);
    const rows = screen.getAllByRole("row").slice(1);
    rows[0].focus();

    await user.keyboard("j");
    expect(rows[1]).toHaveFocus();
    await user.keyboard("j");
    expect(rows[2]).toHaveFocus();
    await user.keyboard("j");
    expect(rows[2]).toHaveFocus();

    await user.keyboard("k");
    expect(rows[1]).toHaveFocus();
    await user.keyboard("k");
    await user.keyboard("k");
    expect(rows[0]).toHaveFocus();
  });

  it("toggles the focused row's selection with x and lifts it to the caller", async () => {
    const user = userEvent.setup();
    wrap(<Harness selectable />);
    const rows = screen.getAllByRole("row").slice(1);
    rows[0].focus();

    await user.keyboard("x");
    expect(screen.getByTestId("selected")).toHaveTextContent("a");
    await user.keyboard("jx");
    expect(screen.getByTestId("selected")).toHaveTextContent("a,b");
    await user.keyboard("x");
    expect(screen.getByTestId("selected")).toHaveTextContent("a");
  });

  it("opens the focused row on Enter", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    wrap(<Harness onOpen={onOpen} />);
    const rows = screen.getAllByRole("row").slice(1);
    rows[0].focus();
    await user.keyboard("j{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(ROWS[1]);
  });

  it("marks selected rows for assistive tech", async () => {
    const user = userEvent.setup();
    wrap(<Harness selectable />);
    const rows = screen.getAllByRole("row").slice(1);
    rows[0].focus();
    await user.keyboard("x");
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(rows[1]).toHaveAttribute("aria-selected", "false");
  });

  it("omits the checkbox column unless selectable", () => {
    wrap(<Harness />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("renders a checkbox per row plus a select-all when selectable", async () => {
    const user = userEvent.setup();
    wrap(<Harness selectable />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(4);
    await user.click(boxes[0]);
    expect(screen.getByTestId("selected")).toHaveTextContent("a,b,c");
    await user.click(boxes[0]);
    expect(screen.getByTestId("selected")).toHaveTextContent("");
  });

  it("drives the saved-view chip row off the URL search params", async () => {
    const user = userEvent.setup();
    wrap(
      <Harness savedViews={[{ id: "all", label: "All" }, { id: "stale", label: "Stale", count: 2 }]} />,
      ["/admin/agencies?view=all"],
    );
    const stale = screen.getByRole("button", { name: /Stale/ });
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(stale).toHaveAttribute("aria-pressed", "false");

    await user.click(stale);
    expect(screen.getByTestId("view")).toHaveTextContent("stale");
    expect(stale).toHaveAttribute("aria-pressed", "true");
  });

  it("renders an empty state instead of a body when there are no rows", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <DataTable caption="Agencies" rows={[]} columns={COLUMNS} rowKey={(r: Row) => r.id} emptyLabel="Nothing" />
        </MemoryRouter>
      </I18nextProvider>,
    );
    expect(screen.getByText("Nothing")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
