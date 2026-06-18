import i18n from "../i18n";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiGet, apiPatch, apiDelete, apiPost } from "./client";
import { ctxToQueryString, type RangeCtx } from "./rangeContext";
import { conversationsAnon } from "./conversationsAnon";
import type {
  Agency,
  AnonThread,
  AppendMessageResult,
  AskResponse,
  Conversation,
  ConvMessage,
  FilterCtx,
  HeatmapCollection,
  NetworkSummary,
  OverviewSummary,
  ReportMeta,
  ReportResponse,
  Route,
  RouteShapeResponse,
  RouteStopProfileResponse,
  RouteSummaryResponse,
  RouteTripsResponse,
} from "./types";
import { useSession } from "./auth";

export function useRoutes(agencyId: number | null): UseQueryResult<Route[]> {
  return useQuery({
    queryKey: ["routes", agencyId],
    queryFn: ({ signal }) => apiGet<Route[]>(`/api/${agencyId}/routes`, { signal }),
    enabled: agencyId != null,
    // Routes are quarterly-static, but a 1-hour staleTime froze empty
    // arrays (returned during a fresh deploy's initial ingest) for an
    // hour; users opened the filter picker and saw "該当なし" until // i18n-ignore: example in comment
    // they reloaded. 5 minutes keeps caching meaningful while letting
    // the picker recover on its own once load_static finishes.
    staleTime: 5 * 60 * 1000,
  });
}

export function useAgencies(): UseQueryResult<Agency[]> {
  return useQuery({
    queryKey: ["agencies"],
    queryFn: ({ signal }) => apiGet<Agency[]>("/api/agencies", { signal }),
    staleTime: 5 * 60 * 1000,
  });
}

export function useReports(agencyId: number | null): UseQueryResult<ReportMeta[]> {
  return useQuery({
    queryKey: ["reports", agencyId],
    queryFn: ({ signal }) => apiGet<ReportMeta[]>(`/api/${agencyId}/reports`, { signal }),
    enabled: agencyId != null,
  });
}

function ctxKey(ctx: RangeCtx) {
  // All filter dimensions must be in the cache key — missing routes/service
  // here would silently serve stale data when those filters change.
  return [ctx.from, ctx.to, ctx.dow, ctx.time_band, ctx.service, ctx.routes.join(",")];
}

export function useReport(
  agencyId: number | null,
  reportType: string | null,
  ctx: RangeCtx,
): UseQueryResult<ReportResponse> {
  return useQuery({
    queryKey: ["reports", agencyId, reportType, ...ctxKey(ctx)],
    queryFn: ({ signal }) =>
      apiGet<ReportResponse>(`/api/${agencyId}/reports/${reportType}?${ctxToQueryString(ctx)}`, { signal }),
    enabled: agencyId != null && !!reportType,
  });
}

export function useOverviewSummary(
  agencyId: number | null,
  ctx: RangeCtx,
): UseQueryResult<OverviewSummary> {
  return useQuery({
    queryKey: ["overview-summary", agencyId, ...ctxKey(ctx)],
    queryFn: ({ signal }) =>
      apiGet<OverviewSummary>(`/api/${agencyId}/overview/summary?${ctxToQueryString(ctx)}`, { signal }),
    enabled: agencyId != null,
  });
}

export function useNetworkSummary(ctx: RangeCtx): UseQueryResult<NetworkSummary> {
  return useQuery({
    queryKey: ["network-summary", ctx.from, ctx.to],
    queryFn: ({ signal }) =>
      apiGet<NetworkSummary>(`/api/network/summary?from=${ctx.from}&to=${ctx.to}`, { signal }),
    staleTime: 60 * 1000,
    // Keep the prior range's table mounted while the new range loads, so stepping
    // the date pickers doesn't flicker the whole board through a Skeleton each change.
    placeholderData: keepPreviousData,
  });
}

export function useHeatmap(
  agencyId: number | null,
  ctx: RangeCtx,
): UseQueryResult<HeatmapCollection> {
  return useQuery({
    queryKey: ["heatmap", agencyId, ...ctxKey(ctx)],
    queryFn: ({ signal }) =>
      apiGet<HeatmapCollection>(`/api/${agencyId}/delays/heatmap?${ctxToQueryString(ctx)}`, { signal }),
    enabled: agencyId != null,
    staleTime: 60 * 1000,
  });
}

export function useRouteShape(
  agencyId: number | null,
  route: string | null,
  ctx: RangeCtx,
): UseQueryResult<RouteShapeResponse> {
  return useQuery({
    queryKey: ["route_shape", agencyId, route, ...ctxKey(ctx)],
    queryFn: ({ signal }) => {
      const qs = new URLSearchParams(ctxToQueryString(ctx));
      qs.set("route", route!);
      return apiGet<RouteShapeResponse>(`/api/${agencyId}/route-shape?${qs.toString()}`, { signal });
    },
    enabled: agencyId != null && !!route,
    staleTime: 60 * 1000,
  });
}

export function useTodayRouteSummary(
  agencyId: number | null,
  options: { autoRefresh: boolean } = { autoRefresh: true },
): UseQueryResult<RouteSummaryResponse> {
  return useQuery({
    queryKey: ["today_route_summary", agencyId],
    queryFn: ({ signal }) => apiGet<RouteSummaryResponse>(`/api/${agencyId}/today/route-summary`, { signal }),
    enabled: agencyId != null,
    refetchInterval: options.autoRefresh ? 30_000 : false,
  });
}

export function useRouteTrips(
  agencyId: number | null,
  routeCode: string | null,
): UseQueryResult<RouteTripsResponse> {
  return useQuery({
    queryKey: ["route_trips", agencyId, routeCode],
    queryFn: ({ signal }) =>
      apiGet<RouteTripsResponse>(
        `/api/${agencyId}/today/route/${encodeURIComponent(routeCode!)}/trips`,
        { signal },
      ),
    enabled: agencyId != null && !!routeCode,
    staleTime: 60 * 1000,
  });
}

export function useRouteStopProfile(
  agencyId: number | null,
  routeCode: string | null,
): UseQueryResult<RouteStopProfileResponse> {
  return useQuery({
    queryKey: ["route_stop_profile", agencyId, routeCode],
    queryFn: ({ signal }) =>
      apiGet<RouteStopProfileResponse>(
        `/api/${agencyId}/today/route/${encodeURIComponent(routeCode!)}/stop-profile`,
        { signal },
      ),
    enabled: agencyId != null && !!routeCode,
    staleTime: 60 * 1000,
  });
}

type CreateAgencyBody = Omit<Agency, "agency_id">;

export function useCreateAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAgencyBody) => apiPost<Agency>("/api/agencies", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

// ─── Conversation + chip hooks ───────────────────────────────────────────────

/**
 * True when the current session is authenticated.
 * Reuses the `useSession` hook from auth.ts (returns null on 401).
 */
export function useIsAuthenticated(): boolean {
  const { data } = useSession();
  return Boolean(data?.user_id);
}

function toServerLikeConversation(t: AnonThread): Conversation {
  return {
    conversation_id: t.client_id,
    user_id: null,
    agency_id: t.agency_id,
    title: t.title,
    filter_ctx: t.filter_ctx,
    pinned: t.pinned,
    created_at: t.created_at,
    updated_at: t.updated_at,
  };
}

export function useConversations(agencyId: number): UseQueryResult<Conversation[]> {
  const authed = useIsAuthenticated();
  return useQuery({
    queryKey: ["conversations", agencyId, authed ? "server" : "anon"],
    queryFn: async ({ signal }) => {
      if (authed) {
        return apiGet<Conversation[]>(`/api/${agencyId}/conversations`, { signal });
      }
      // Anonymous: read from localStorage; shape-convert to Conversation
      return conversationsAnon.list(agencyId).map(toServerLikeConversation);
    },
    staleTime: 5_000,
  });
}

export function useConversation(
  agencyId: number,
  conversationId: string | null,
): UseQueryResult<{ conversation: Conversation; messages: ConvMessage[] } | null> {
  const authed = useIsAuthenticated();
  return useQuery({
    queryKey: ["conversation", agencyId, conversationId, authed ? "server" : "anon"],
    queryFn: async ({ signal }) => {
      if (!conversationId) return null;
      if (authed) {
        const [conv, msgs] = await Promise.all([
          apiGet<Conversation>(`/api/${agencyId}/conversations/${conversationId}`, { signal }),
          apiGet<ConvMessage[]>(`/api/${agencyId}/conversations/${conversationId}/messages`, { signal }),
        ]);
        return { conversation: conv, messages: msgs };
      }
      const anon = conversationsAnon.get(conversationId);
      if (!anon) return null;
      return { conversation: toServerLikeConversation(anon), messages: anon.messages };
    },
    enabled: Boolean(conversationId),
  });
}

export function useCreateConversation(agencyId: number) {
  const authed = useIsAuthenticated();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { title: string; filter_ctx: FilterCtx }) => {
      if (authed) {
        return apiPost<Conversation>(`/api/${agencyId}/conversations`, vars);
      }
      const anon = conversationsAnon.create(agencyId, vars.title, vars.filter_ctx);
      return toServerLikeConversation(anon);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations", agencyId] }),
  });
}

export function useUpdateConversation(agencyId: number) {
  const authed = useIsAuthenticated();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      id: string;
      patch: Partial<Pick<Conversation, "title" | "pinned" | "filter_ctx">>;
    }) => {
      if (authed) {
        return apiPatch<Conversation>(`/api/${agencyId}/conversations/${vars.id}`, vars.patch);
      }
      const updated = conversationsAnon.update(vars.id, vars.patch);
      if (!updated) throw new Error("thread not found");
      return toServerLikeConversation(updated);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations", agencyId] });
      qc.invalidateQueries({ queryKey: ["conversation", agencyId] });
    },
  });
}

export function useDeleteConversation(agencyId: number) {
  const authed = useIsAuthenticated();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      if (authed) {
        return apiDelete<{ ok: true }>(`/api/${agencyId}/conversations/${id}`);
      }
      conversationsAnon.delete(id);
      return { ok: true as const };
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations", agencyId] }),
  });
}

/** Build a user-bubble label for a structured (builder) submission that's
 *  fully translated — no raw ``metric=avg_delay`` machine strings.
 *  Reads from ``ask.build_labels`` in the active locale.
 */
function builderSummary(tool: string, args: Record<string, unknown>): string {
  const t = i18n.t.bind(i18n);
  const toolLabel = t(`ask.build_labels.tools.${tool}`, { defaultValue: tool });
  const pairs: string[] = [];
  for (const [k, v] of Object.entries(args).slice(0, 4)) {
    const keyLabel = t(`ask.build_labels.${k}`, { defaultValue: k });
    let valLabel: string;
    if (typeof v === "string") {
      valLabel = t(`ask.build_labels.values.${v}`, { defaultValue: v });
    } else if (typeof v === "boolean") {
      valLabel = v ? t("common.yes") : t("common.no");
    } else {
      valLabel = String(v);
    }
    pairs.push(`${keyLabel}: ${valLabel}`);
  }
  return `🛠 ${toolLabel}` + (pairs.length ? ` (${pairs.join(", ")})` : "");
}

type AppendMessageVars = {
  conversationId: string;
  tool: string;
  args: Record<string, unknown>;
  /** Human-readable label for the user bubble (e.g. from card buildSummary).
   *  When present, sent as `user_summary` to the server and used as the
   *  rendered_summary for the anonymous path instead of the machine-generated
   *  builderSummary() fallback. */
  user_summary?: string;
  /** Current filter context (date range + routes). Forwarded as `ctx` in the
   *  anonymous POST /ask body so the backend scopes the query correctly. */
  filter_ctx?: FilterCtx;
};

export function useAppendMessage(agencyId: number) {
  const authed = useIsAuthenticated();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: AppendMessageVars): Promise<AppendMessageResult> => {
      // Use caller-supplied user_summary (card's buildSummary) when
      // available; fall back to the machine-generated builderSummary() only if
      // absent.  This ensures the user bubble shows a human-readable label
      // instead of raw key=value machine strings.
      const humanLabel = vars.user_summary ?? builderSummary(vars.tool, vars.args);

      if (authed) {
        return apiPost<AppendMessageResult>(
          `/api/${agencyId}/conversations/${vars.conversationId}/messages`,
          { tool: vars.tool, args: vars.args, user_summary: humanLabel },
        );
      }
      // Anonymous path: dispatch via POST /ask with __build__ sentinel.
      const dispatchTool = vars.tool;
      const dispatchArgs = vars.args;
      const chipTitle = humanLabel;

      const question = `__build__ ${dispatchTool} ${JSON.stringify(dispatchArgs)}`;
      // Forward filter context so the backend scopes the query to the user's
      // chosen date range and route list (scenario 08 — filter mid-thread).
      const fc = vars.filter_ctx;
      const askBody: Record<string, unknown> = { question };
      if (fc) {
        askBody.ctx = {
          from: fc.from_date,
          to: fc.to_date,
          dow: fc.dow ?? "all",
          time_band: fc.time_band ?? "all",
          service: fc.service ?? "all",
          routes: fc.routes ?? [],
        };
      }
      const askResp = await apiPost<AskResponse>(`/api/${agencyId}/ask`, askBody);

      const now = new Date().toISOString();
      const baseId = -(Date.now());
      const userMsg: ConvMessage = {
        message_id: baseId,
        conversation_id: vars.conversationId,
        role: "user",
        chip_id: null,
        tool: dispatchTool,
        args: dispatchArgs,
        signature_hash: null,
        result: null,
        rendered_summary: chipTitle,
        created_at: now,
      };
      const asstMsg: ConvMessage = {
        message_id: baseId - 1,
        conversation_id: vars.conversationId,
        role: "assistant",
        chip_id: null,
        tool: dispatchTool,
        args: askResp.canonical_args ?? dispatchArgs,
        signature_hash: askResp.signature_hash ?? null,
        result: askResp.result
          ? {
              kind: askResp.result.kind,
              summary: askResp.result.summary ?? null,
              rows: (askResp.result.rows as unknown[] | undefined) ?? null,
              columns: askResp.result.columns ?? null,
              series: askResp.result.series ?? null,
              pairs: askResp.result.pairs ?? null,
            }
          : null,
        rendered_summary: askResp.answer ?? null,
        created_at: now,
      };
      conversationsAnon.appendMessage(vars.conversationId, userMsg);
      conversationsAnon.appendMessage(vars.conversationId, asstMsg);
      return { user: userMsg, assistant: asstMsg };
    },
    onSuccess: (_result: AppendMessageResult, vars: AppendMessageVars) => {
      qc.invalidateQueries({ queryKey: ["conversation", agencyId, vars.conversationId] });
      qc.invalidateQueries({ queryKey: ["conversations", agencyId] });
    },
  });
}

export function useFollowupEnabled(agencyId: number | null) {
  return useQuery({
    queryKey: ["ask-followup-enabled", agencyId],
    queryFn: ({ signal }) =>
      apiGet<{ enabled: boolean }>(`/api/${agencyId}/ask/followup-enabled`, { signal }),
    enabled: agencyId != null,
    staleTime: 60 * 60 * 1000,
  });
}

export function useFollowup(agencyId: number, authed: boolean) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      conversationId: string;
      contextMessageId: number;
      question: string;
    }) => {
      if (authed) {
        return apiPost<AppendMessageResult>(
          `/api/${agencyId}/conversations/${vars.conversationId}/followup`,
          { question: vars.question, context_message_id: vars.contextMessageId },
        );
      }
      // Anon path: look up context message from localStorage
      const localThread = conversationsAnon.get(vars.conversationId);
      const ctx = localThread?.messages.find((m) => m.message_id === vars.contextMessageId);
      if (!ctx) throw new Error("local context message not found");
      const resp = await apiPost<AppendMessageResult>(
        `/api/${agencyId}/conversations/${vars.conversationId}/followup`,
        {
          question: vars.question,
          context_tool: ctx.tool ?? null,
          context_args: ctx.args ?? null,
          context_result: ctx.result ?? null,
        },
      );
      // Persist synthetic messages to localStorage
      conversationsAnon.appendMessage(vars.conversationId, resp.user);
      conversationsAnon.appendMessage(vars.conversationId, resp.assistant);
      return resp;
    },
    onSuccess: (_result, vars) => {
      if (authed) {
        qc.invalidateQueries({ queryKey: ["conversation", agencyId, vars.conversationId] });
        qc.invalidateQueries({ queryKey: ["conversations", agencyId] });
      } else {
        // Anon: invalidate the local conversation query so UI re-renders
        qc.invalidateQueries({ queryKey: ["conversation", agencyId, vars.conversationId] });
        qc.invalidateQueries({ queryKey: ["conversations", agencyId] });
      }
    },
  });
}

export function useMigrateAnon(agencyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const threads = conversationsAnon.exportAll();
      if (threads.length === 0) return { inserted: 0 };
      const r = await apiPost<{ inserted: number }>(
        `/api/${agencyId}/conversations/migrate-anon`,
        { threads },
      );
      if (r.inserted > 0) conversationsAnon.clearAll();
      return r;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations", agencyId] }),
  });
}
