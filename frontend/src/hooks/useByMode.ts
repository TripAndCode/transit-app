import { useSearchParams } from "react-router-dom";

/** The view a screen picks from its `by` param, read by *parse*, and a setter
 *  that writes `by` and keeps every other param. */
export function useByMode<M extends string>(parse: (by: string | null) => M): [M, (next: M) => void] {
  const [params, setParams] = useSearchParams();
  return [
    parse(params.get("by")),
    (next) =>
      setParams((prev) => {
        const n = new URLSearchParams(prev);
        n.set("by", next);
        return n;
      }),
  ];
}
