import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "../../i18n";
import { ReportList } from "./ReportList";

beforeEach(async () => await i18n.changeLanguage("en"));

describe("ReportList", () => {
  it("groups the entries under their group heading, in reading order", () => {
    render(
      <ReportList
        types={["trend", "delay_certificate", "ranking", "on_time"]}
        active={null}
        onSelect={vi.fn()}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(headings).toEqual(["Rankings", "Punctuality", "Patterns over time", "Forecasts and evidence"]);
    const rankings = screen.getByRole("group", { name: "Rankings" });
    expect(within(rankings).getAllByRole("button")).toHaveLength(1);
  });

  it("gives every entry a one-line description under its name", () => {
    render(<ReportList types={["ranking"]} active={null} onSelect={vi.fn()} />);
    const entry = screen.getByRole("button", { name: /Delay ranking/ });
    expect(entry).toHaveTextContent("Delay ranking");
    expect(entry).toHaveTextContent("Which routes run latest, worst first.");
  });

  it("renders route_forecast from the data, not as a hand-copied extra button", async () => {
    const onSelect = vi.fn();
    render(<ReportList types={["ranking", "route_forecast"]} active={null} onSelect={onSelect} />);
    const forecast = screen.getByRole("button", { name: /Route forecast/ });
    await userEvent.click(forecast);
    expect(onSelect).toHaveBeenCalledWith("route_forecast");
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("marks the active entry pressed and nothing else", () => {
    render(<ReportList types={["ranking", "trend"]} active="trend" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Trend/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Delay ranking/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("still lists a report type this build does not recognise", () => {
    render(<ReportList types={["brand_new"]} active={null} onSelect={vi.fn()} />);
    expect(screen.getByRole("group", { name: "Other" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /brand_new/ })).toBeInTheDocument();
  });
});
