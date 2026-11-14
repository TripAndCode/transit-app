import { useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import type { SnapshotSpec } from "../../api/snapshots";
import type { ReportResponse } from "../../api/types";
import { contentLocale } from "../../i18n/contentLocale";
import { usePrintFootSlot } from "../printFootSlot";
import { snapshotForPrint, type PrintSnapshot } from "./printSnapshot";

/** What one print's snapshot came to, and what it was taken of: the
 *  screen's query and the very response the page showed. */
type Printed = { screen: string; report: ReportResponse; snapshot: PrintSnapshot };

/** One view of the screen: its query and the response it shows. */
type View = { screen: string | null; report: ReportResponse };

function screenKey({ screen, agencyId, query }: SnapshotSpec): string {
  return `${screen}:${agencyId}:${query}`;
}

/** The council report's print action. Given `snapshot`, the screen's own
 *  query (only an admin's screen passes one), a print first freezes the
 *  figures on the page, then prints the snapshot's link and method in the
 *  printed foot, so the paper traces to stored data. A snapshot that cannot
 *  be taken, or that holds figures other than the page's, never holds the
 *  print back: the page prints without a link and says why. The line
 *  belongs to the response it was taken of, so it leaves the foot once the
 *  page shows other figures; a print the browser starts on its own (Ctrl+P)
 *  takes no snapshot. A print whose screen changes while its snapshot is
 *  taken (another scope, new figures, the report gone) is called off: the
 *  page it would print is no longer the one asked for, its header already
 *  naming the new scope. Without `snapshot`, or outside the app shell's
 *  print frame, it prints as it is. */
export function CouncilPrint({ report, snapshot }: { report: ReportResponse; snapshot?: SnapshotSpec | null }) {
  const { t, i18n } = useTranslation();
  const slot = usePrintFootSlot();
  const [pending, setPending] = useState(false);
  const [printed, setPrinted] = useState<Printed | null>(null);
  // The view a print was called off beside: the note belongs to it alone.
  const [cancelledAt, setCancelledAt] = useState<View | null>(null);
  // The pending print's own cancellation, one per click, and the view on
  // screen now, which a call-off names.
  const pendingPrint = useRef<AbortController | null>(null);
  const shown = useRef<View | null>(null);
  const screen = snapshot ? screenKey(snapshot) : null;
  // Another screen or another response, or leaving the page, calls off the
  // print taken for the one before. A layout effect's cleanup runs within the
  // commit, so the call-off lands before the print's own continuation can.
  useLayoutEffect(() => {
    shown.current = { screen, report };
    return () => pendingPrint.current?.abort();
  }, [screen, report]);
  const cancelled = cancelledAt != null && cancelledAt.screen === screen && cancelledAt.report === report;
  const outcome = printed != null && printed.screen === screen && printed.report === report ? printed.snapshot : null;
  const link = outcome?.kind === "stored" ? { url: `${window.location.origin}${outcome.path}`, method: outcome.methodVersion } : null;

  async function print() {
    if (snapshot == null || screen == null || slot == null) {
      window.print();
      return;
    }
    const controller = new AbortController();
    pendingPrint.current = controller;
    setPending(true);
    setCancelledAt(null);
    const taken = await snapshotForPrint(snapshot, report, contentLocale(i18n), controller.signal);
    if (controller.signal.aborted) {
      setPending(false);
      setCancelledAt(shown.current);
      return;
    }
    // print() lays the page out at once, so the foot's line has to be in the
    // document before it is called.
    flushSync(() => {
      setPending(false);
      setPrinted({ screen, report, snapshot: taken });
    });
    window.print();
  }

  return (
    <>
      <button type="button" className="btn btn--secondary btn--sm council-report__print" disabled={pending} onClick={() => void print()}>
        {t("reports.print")}
      </button>
      {pending && (
        <p role="status" className="council-report__print-note">
          {t("reports.council.print_snapshot.pending")}
        </p>
      )}
      {cancelled && !pending && (
        <p role="status" className="council-report__print-note">
          {t("reports.council.print_snapshot.cancelled")}
        </p>
      )}
      {outcome && !pending && !cancelled && (
        <p role="status" className="council-report__print-note">
          {link ? (
            <>
              {t("reports.council.print_snapshot.stored")} <a href={link.url}>{link.url}</a>
            </>
          ) : (
            t(`reports.council.print_snapshot.${outcome.kind}`)
          )}
        </p>
      )}
      {link && slot && createPortal(t("print.snapshot", link), slot)}
    </>
  );
}
