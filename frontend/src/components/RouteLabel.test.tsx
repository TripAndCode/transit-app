import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import i18n from "../i18n";
import { buildRouteNames } from "../api/useRouteNames";
import type { Route } from "../api/types";
import { RouteLabel, RoutePartsLabel } from "./RouteLabel";

void i18n.changeLanguage("en");

function route(code: string, fields: Partial<Route>): Route {
  return { route_id: code, route_short_name: null, route_long_name: null, route_code: code, trip_headsigns: [], ...fields };
}

const names = buildRouteNames(
  [
    route("1471981638", { route_short_name: "70-3", route_long_name: "あさひが丘線", origin_name: "広島バスセンター", destination_name: "あさひが丘下" }),
    route("1237145910", {
      route_short_name: "54-1",
      route_long_name: "西広島バイパス線",
      trip_headsigns: ["市役所経由"],
      origin_name: "広島バスセンター",
      via_name: "市役所",
      destination_name: "山田団地車庫前",
    }),
    route("316470308", { route_short_name: "55-2", route_long_name: "西広島バイパス線" }),
    route("1757472590", { route_short_name: "54-1", route_long_name: "", trip_headsigns: ["市役所経由"] }),
    route("1757472591", { route_short_name: "54-1", route_long_name: "", trip_headsigns: ["己斐経由"] }),
    route("70", { route_short_name: "70-3", route_long_name: "", trip_headsigns: ["市役所経由"] }),
    route("360868713", { route_short_name: "3", route_long_name: "3号線", origin_name: "広島駅", destination_name: "観音新町三丁目", bidirectional: true }),
    route("488396710", { route_short_name: "11", route_long_name: "広仁方線", origin_name: "呉駅前", is_loop: true }),
    route("2", { route_short_name: "2-5", route_long_name: "2-5" }),
    route("1021", { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "国道・古川線" }),
    route("10012", { route_id: "国道・古川線(10012)", route_short_name: "A1 国道・古川線", route_long_name: "", trip_headsigns: ["A1明の星→青森駅（国道・古川線）"] }),
    route("12212", { route_id: "国道・古川線(12212)", route_short_name: "A1 国道・古川線", route_long_name: "", trip_headsigns: ["A1中央大橋→青森駅（国道・古川線）"] }),
    route("9", { route_id: "国道・古川線(9)", route_short_name: "", route_long_name: "" }),
  ],
  i18n.t,
);

describe("RouteLabel", () => {
  it("draws the short code as a plate, the line name beside it, and the terminals beneath", () => {
    const { container } = render(<RouteLabel code="1471981638" names={names} />);
    expect(container.querySelector(".route-label__plate")).toHaveTextContent(/^70-3$/);
    expect(container.querySelector(".route-label__line")).toHaveTextContent(/^あさひが丘線$/);
    expect(container.querySelector(".route-label__ends")).toHaveTextContent(/^広島バスセンター → あさひが丘下$/);
    expect(container).toHaveTextContent(/^70-3 あさひが丘線 広島バスセンター → あさひが丘下$/);
  });

  it("lets each line of the full label wrap once and clamp, rather than cut a Japanese name mid-word", () => {
    // Guards the clamp the source scan in styles/clamp.test.ts cannot see:
    // the line name's class is chosen by the variant.
    const { container } = render(<RouteLabel code="1471981638" names={names} />);
    expect(container.querySelector(".route-label__line")).toHaveClass("clamp-2");
    expect(container.querySelector(".route-label__ends")).toHaveClass("clamp-2");
    const compact = render(<RouteLabel code="1471981638" names={names} compact />);
    expect(compact.container.querySelector(".route-label__line")).not.toHaveClass("clamp-2");
  });

  it("keeps the route_id out of the text, in a tooltip", () => {
    const { container } = render(<RouteLabel code="1471981638" names={names} />);
    expect(container).not.toHaveTextContent("1471981638");
    expect(container.querySelector(".route-label")).toHaveAttribute("title", "1471981638");
  });

  it("adds the via point after the terminals, quietly", () => {
    const { container } = render(<RouteLabel code="1237145910" names={names} />);
    expect(container.querySelector(".route-label__ends")).toHaveTextContent("広島バスセンター → 山田団地車庫前 · via 市役所");
    expect(container.querySelector(".route-label__via")).toHaveTextContent("via 市役所");
  });

  it("names the via point in Japanese word order", async () => {
    await i18n.changeLanguage("ja");
    const ja = buildRouteNames([route("1237145910", { route_short_name: "54-1", origin_name: "広島バスセンター", via_name: "市役所", destination_name: "山田団地車庫前" })], i18n.t);
    const { container } = render(<RouteLabel code="1237145910" names={ja} />);
    expect(container.querySelector(".route-label__via")).toHaveTextContent("市役所経由");
    await i18n.changeLanguage("en");
  });

  it("marks a route that runs both ways, and a loop", () => {
    expect(render(<RouteLabel code="360868713" names={names} />).container.querySelector(".route-label__ends")).toHaveTextContent(
      /^広島駅 ⇄ 観音新町三丁目$/,
    );
    expect(render(<RouteLabel code="488396710" names={names} />).container.querySelector(".route-label__ends")).toHaveTextContent(
      /^Loop from 呉駅前$/,
    );
  });

  it("uses a headsign only to tell apart routes that read the same, as a via point when it names one", () => {
    const { container } = render(<RouteLabel code="1757472590" names={names} />);
    expect(container).toHaveTextContent(/^54-1 via 市役所$/);
    expect(container.querySelector(".route-label__via")).toHaveTextContent(/^via 市役所$/);
    expect(container).not.toHaveTextContent("行き");
    // 70-3 has the same headsign, but no other route reads "70-3".
    expect(render(<RouteLabel code="70" names={names} />).container).toHaveTextContent(/^70-3$/);
  });

  it("has no second line when nothing says where the route runs", () => {
    const { container } = render(<RouteLabel code="316470308" names={names} />);
    expect(container).toHaveTextContent(/^55-2 西広島バイパス線$/);
    expect(container.querySelector(".route-label__ends")).toBeNull();
  });

  it("shows the plate alone when the line name only repeats it", () => {
    const { container } = render(<RouteLabel code="2" names={names} />);
    expect(container).toHaveTextContent(/^2-5$/);
    expect(container.querySelector(".route-label__line")).toBeNull();
  });

  it("shows the line name alone, without a plate, when the route has no short code", () => {
    const { container } = render(<RouteLabel code="1021" names={names} />);
    expect(container).toHaveTextContent(/^国道・古川線$/);
    expect(container.querySelector(".route-label__plate")).toBeNull();
  });

  it("reads an Aomori name that carries its own code like the rest, its headsign telling the variants apart", () => {
    const { container } = render(<RouteLabel code="10012" names={names} />);
    expect(container.querySelector(".route-label__plate")).toHaveTextContent(/^A1$/);
    expect(container.querySelector(".route-label__line")).toHaveTextContent(/^国道・古川線$/);
    expect(container.querySelector(".route-label__ends")).toHaveTextContent(/^明の星→青森駅$/);
  });

  it("falls back to the route_id when neither name is set", () => {
    const { container } = render(<RouteLabel code="9" names={names} />);
    expect(container).toHaveTextContent(/^国道・古川線\(9\)$/);
  });

  it("in its compact form, keeps to one line: plate and line name, with where it runs in the tooltip", () => {
    const { container } = render(<RouteLabel code="1471981638" names={names} compact />);
    expect(container).toHaveTextContent(/^70-3 あさひが丘線$/);
    expect(container.querySelector(".route-label__ends")).toBeNull();
    expect(container.querySelector(".route-label")).toHaveAttribute("title", "広島バスセンター → あさひが丘下 · 1471981638");
  });

  it("in its compact form, still says where a route runs when another route shares its name", () => {
    const { container } = render(<RouteLabel code="10012" names={names} compact />);
    expect(container).toHaveTextContent(/^A1 国道・古川線 · 明の星→青森駅$/);
    expect(container.querySelector(".route-label__aside")).toHaveTextContent("明の星→青森駅");
    expect(render(<RouteLabel code="316470308" names={names} compact />).container.querySelector(".route-label__aside")).toBeNull();
  });

  it("leaves where a route runs out of a compact label when the caller shows it beside the label", () => {
    const parts = names.parts.get("10012")!;
    const { container } = render(<RoutePartsLabel parts={parts} compact nameOnly />);
    expect(container).toHaveTextContent(/^A1 国道・古川線$/);
  });

  it("uses the caller's name until the static routes know the code", () => {
    const empty = buildRouteNames([], i18n.t);
    const { container } = render(<RouteLabel code="1471981638" names={empty} fallbackName="70-3" />);
    expect(container.querySelector(".route-label__plate")).toHaveTextContent(/^70-3$/);
    const long = render(<RouteLabel code="1471981638" names={empty} fallbackName="あさひが丘線" />);
    expect(long.container.querySelector(".route-label__plate")).toBeNull();
    expect(long.container).toHaveTextContent(/^あさひが丘線$/);
  });

  it("reads as Route <code> when nothing names it", () => {
    const { container } = render(<RouteLabel code="53011" names={names} />);
    expect(container).toHaveTextContent(/^Route 53011$/);
  });

  describe("in a list's plate column", () => {
    it("marks a plated label and a plate-less one alike, so the list can line their names up", () => {
      const plated = render(<RouteLabel code="1471981638" names={names} plateColumn />);
      expect(plated.container.firstElementChild).toHaveClass("route-label--plate-column");
      expect(plated.container.querySelector(".route-label__plate")).toHaveTextContent(/^70-3$/);
      const bare = render(<RouteLabel code="360868713" names={names} plateColumn />);
      expect(bare.container.firstElementChild).toHaveClass("route-label--plate-column");
      expect(bare.container.querySelector(".route-label__plate")).toBeNull();
      expect(bare.container.querySelector(".route-label__line")).toHaveTextContent(/^3号線$/);
    });

    it("gives a label nothing names an element of its own, so it takes the name's column, not the plate's", () => {
      const { container } = render(<RouteLabel code="53011" names={names} plateColumn />);
      const label = container.firstElementChild;
      expect(label).toHaveClass("route-label", "route-label--plate-column");
      expect(label?.querySelector(".route-label__line")).toHaveTextContent(/^Route 53011$/);
    });

    it("leaves a label outside such a list as it was", () => {
      const { container } = render(<RouteLabel code="1471981638" names={names} />);
      expect(container.firstElementChild).not.toHaveClass("route-label--plate-column");
      const bare = render(<RouteLabel code="53011" names={names} />);
      expect(bare.container.firstElementChild).toBeNull();
    });
  });
});

