import { useEffect, useState } from "react";
import i18n from "../i18n";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet, apiPatch, apiDelete, apiPost } from "./client";
import { ctxToQueryString, type RangeCtx } from "./rangeContext";
import { conversationsAnon } from "./conversationsAnon";
import type {
  Agency,
  AnonThread,
  AppendMessageResult,
  AskResponse,
  BuildSchema,
  ChipTemplate,
  Conversation,
  ConvMessage,
  EditAction,
  FilterCtx,
  HeatmapCollection,
  OverviewSummary,
  ReportMeta,
  ReportResponse,
  Route,
  RouteShapeResponse,
  RouteSummaryResponse,
  SuggestItem,
} from "./types";
import { useSession } from "./auth";

export function useRoutes(agencyId: number | null): UseQueryResult<Route[]> {
  return useQuery({
    queryKey: ["routes", agencyId],
    queryFn: () => apiGet<Route[]>(`/api/${agencyId}/routes`),
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
    queryFn: () => apiGet<Agency[]>("/api/agencies"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useReports(agencyId: number | null): UseQueryResult<ReportMeta[]> {
  return useQuery({
    queryKey: ["reports", agencyId],
    queryFn: () => apiGet<ReportMeta[]>(`/api/${agencyId}/reports`),
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
    queryFn: () =>
      apiGet<ReportResponse>(`/api/${agencyId}/reports/${reportType}?${ctxToQueryString(ctx)}`),
    enabled: agencyId != null && !!reportType,
  });
}

export function useOverviewSummary(
  agencyId: number | null,
  ctx: RangeCtx,
): UseQueryResult<OverviewSummary> {
  return useQuery({
    queryKey: ["overview-summary", agencyId, ...ctxKey(ctx)],
    queryFn: () =>
      apiGet<OverviewSummary>(`/api/${agencyId}/overview/summary?${ctxToQueryString(ctx)}`),
    enabled: agencyId != null,
  });
}

export function useHeatmap(
  agencyId: number | null,
  ctx: RangeCtx,
): UseQueryResult<HeatmapCollection> {
  return useQuery({
    queryKey: ["heatmap", agencyId, ...ctxKey(ctx)],
    queryFn: () => apiGet<HeatmapCollection>(`/api/${agencyId}/delays/heatmap?${ctxToQueryString(ctx)}`),
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
    queryFn: () => {
      const qs = new URLSearchParams(ctxToQueryString(ctx));
      qs.set("route", route!);
      return apiGet<RouteShapeResponse>(`/api/${agencyId}/route-shape?${qs.toString()}`);
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
    queryFn: () => apiGet<RouteSummaryResponse>(`/api/${agencyId}/today/route-summary`),
    enabled: agencyId != null,
    refetchInterval: options.autoRefresh ? 30_000 : false,
  });
}

export function useAsk(agencyId: number | null) {
  const { t } = useTranslation();
  return useMutation({
    mutationFn: (vars: {
      question: string;
      ctx: RangeCtx;
      history?: { question: string; tool?: string | null; args?: Record<string, unknown> | null }[];
    }) => {
      if (agencyId == null) {
        return Promise.reject(new Error(t("ask.error_agency_not_selected")));
      }
      return apiPost<AskResponse>(`/api/${agencyId}/ask`, {
        question: vars.question,
        ctx: {
          from: vars.ctx.from,
          to: vars.ctx.to,
          dow: vars.ctx.dow,
          time_band: vars.ctx.time_band,
          service: vars.ctx.service,
          routes: vars.ctx.routes,
        },
        history: vars.history ?? [],
      });
    },
  });
}

export type CreateAgencyBody = Omit<Agency, "agency_id">;

export function useCreateAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAgencyBody) => apiPost<Agency>("/api/agencies", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

// ─── Phase ② hooks ───────────────────────────────────────────────────────────

/**
 * Debounced autocomplete suggestions for the Ask input.
 *
 * The debounce (150 ms) is handled inside this hook — callers may pass the
 * live, un-debounced `q` directly.  The query is skipped when the trimmed
 * value is shorter than two characters to avoid noisy round-trips.
 */
export function useAskSuggest(
  q: string,
  agencyId: number,
): UseQueryResult<SuggestItem[]> {
  const [debouncedQ, setDebouncedQ] = useState(q);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 150);
    return () => clearTimeout(id);
  }, [q]);

  return useQuery({
    queryKey: ["ask-suggest", agencyId, debouncedQ],
    queryFn: () =>
      apiGet<SuggestItem[]>(
        `/api/${agencyId}/ask/suggest?q=${encodeURIComponent(debouncedQ)}&limit=8`,
      ),
    // Empty q is a valid "top-hits chip-set" query (server returns the
    // most-hit cache rows). Short non-empty q would just be noise, so gate
    // those out.
    enabled: debouncedQ.trim().length === 0 || debouncedQ.trim().length >= 2,
    // NN distances against rag_chunks are stable between deploys — 5 min
    // stale-time prevents redundant round-trips while the user is typing.
    staleTime: 5 * 60 * 1000,
    placeholderData: (prev) => prev,
  });
}

/**
 * Fetches the tool schema used by the guided build form.
 *
 * The schema only changes on deploy, so it is treated as effectively
 * immutable at runtime (staleTime: Infinity).
 */
export function useAskBuildSchema(agencyId: number): UseQueryResult<BuildSchema> {
  return useQuery({
    queryKey: ["ask-build-schema", agencyId],
    queryFn: () => apiGet<BuildSchema>(`/api/${agencyId}/ask/build-schema`),
    staleTime: Infinity,
  });
}

/**
 * Records whether the user confirmed or edited a canonical intent suggestion.
 * Fires a POST to `/api/{agencyId}/ask/edit-action`.
 */
export function usePostEditAction(
  agencyId: number,
): UseMutationResult<{ ok: true }, Error, { signature_hash: string; action: EditAction }> {
  return useMutation({
    mutationFn: (body: { signature_hash: string; action: EditAction }) =>
      apiPost<{ ok: true }>(`/api/${agencyId}/ask/edit-action`, body),
  });
}

// ─── Phase ③ hooks — conversations + chips ───────────────────────────────────

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

function findChip(schema: BuildSchema | undefined, chipId: string): ChipTemplate | undefined {
  if (!schema?.chips) return undefined;
  for (const chips of Object.values(schema.chips)) {
    const found = chips.find((c) => c.id === chipId);
    if (found) return found;
  }
  return undefined;
}

export function useConversations(agencyId: number): UseQueryResult<Conversation[]> {
  const authed = useIsAuthenticated();
  return useQuery({
    queryKey: ["conversations", agencyId, authed ? "server" : "anon"],
    queryFn: async () => {
      if (authed) {
        return apiGet<Conversation[]>(`/api/${agencyId}/conversations`);
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
    queryFn: async () => {
      if (!conversationId) return null;
      if (authed) {
        const [conv, msgs] = await Promise.all([
          apiGet<Conversation>(`/api/${agencyId}/conversations/${conversationId}`),
          apiGet<ConvMessage[]>(`/api/${agencyId}/conversations/${conversationId}/messages`),
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
      valLabel = v ? t("common.yes", { defaultValue: "はい" }) : t("common.no", { defaultValue: "いいえ" });
    } else {
      valLabel = String(v);
    }
    pairs.push(`${keyLabel}: ${valLabel}`);
  }
  return `🛠 ${toolLabel}` + (pairs.length ? ` (${pairs.join(", ")})` : "");
}

// Vars for the chip path
type AppendByChip = {
  conversationId: string;
  chip_id: string;
  args_override?: Record<string, unknown>;
  tool?: never;
  args?: never;
};

// Vars for the builder direct-dispatch path
type AppendByTool = {
  conversationId: string;
  chip_id?: never;
  args_override?: never;
  tool: string;
  args: Record<string, unknown>;
};

export type AppendMessageVars = AppendByChip | AppendByTool;

export function useAppendMessage(agencyId: number) {
  const authed = useIsAuthenticated();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: AppendMessageVars): Promise<AppendMessageResult> => {
      if (authed) {
        if (vars.chip_id !== undefined) {
          return apiPost<AppendMessageResult>(
            `/api/${agencyId}/conversations/${vars.conversationId}/messages`,
            { chip_id: vars.chip_id, args_override: vars.args_override },
          );
        } else {
          return apiPost<AppendMessageResult>(
            `/api/${agencyId}/conversations/${vars.conversationId}/messages`,
            { tool: vars.tool, args: vars.args, user_summary: builderSummary(vars.tool, vars.args) },
          );
        }
      }
      // Anonymous path: dispatch via POST /ask with __build__ sentinel.
      let dispatchTool: string;
      let dispatchArgs: Record<string, unknown>;
      let chipTitle: string;
      let chipId: string | null;

      if (vars.chip_id !== undefined) {
        const schema = qc.getQueryData<BuildSchema>(["ask-build-schema", agencyId]);
        const chip = findChip(schema, vars.chip_id);
        if (!chip) throw new Error(`unknown chip ${vars.chip_id}`);
        dispatchTool = chip.tool;
        dispatchArgs = { ...chip.args, ...(vars.args_override ?? {}) };
        chipTitle = chip.title;
        chipId = vars.chip_id;
      } else {
        dispatchTool = vars.tool;
        dispatchArgs = vars.args;
        // Use the localized summary helper instead of raw key=value joins.
        chipTitle = builderSummary(dispatchTool, dispatchArgs);
        chipId = null;
      }

      const question = `__build__ ${dispatchTool} ${JSON.stringify(dispatchArgs)}`;
      const askResp = await apiPost<AskResponse>(`/api/${agencyId}/ask`, { question });

      const now = new Date().toISOString();
      const baseId = -(Date.now());
      const userMsg: ConvMessage = {
        message_id: baseId,
        conversation_id: vars.conversationId,
        role: "user",
        chip_id: chipId,
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
        chip_id: chipId,
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

/**
 * Full chip catalog from /ask/build-schema — includes tools and all chip categories.
 * Effectively immutable at runtime (staleTime: Infinity).
 * Note: also used as the chip lookup cache by useAppendMessage (anon path).
 */
export function useChipCatalog(agencyId: number): UseQueryResult<BuildSchema> {
  return useQuery({
    queryKey: ["ask-build-schema", agencyId],
    queryFn: () => apiGet<BuildSchema>(`/api/${agencyId}/ask/build-schema`),
    staleTime: Infinity,
  });
}

/**
 * Popular chips ordered by server hit_count.
 * Used as the default chip tray on empty conversation state.
 */
export function usePopularChips(agencyId: number, limit = 6): UseQueryResult<ChipTemplate[]> {
  return useQuery({
    queryKey: ["popular-chips", agencyId, limit],
    queryFn: () =>
      apiGet<ChipTemplate[]>(`/api/${agencyId}/ask/popular-chips?limit=${limit}`),
    staleTime: 60_000,
  });
}
