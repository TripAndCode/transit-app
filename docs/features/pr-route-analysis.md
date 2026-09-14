Route analysis now focuses on one service pattern: a stop-delay chart, selected
stop, a small map and optional raw observations. Route and keito filters share
the query scope with CSV and saved analysis bookmarks.

Optional previous-week comparison matches stop id and sequence; missing stops
remain gaps. Labels state the API's actual **mean departure delay** metric,
not the mockup's median. Differences between stop means do not establish delay
growth for an individual trip or a cause; that limitation is visible.

Saved analyses are browser-local filter bookmarks, not immutable snapshots.
The existing route-shape endpoint chooses the representative observed pattern.
