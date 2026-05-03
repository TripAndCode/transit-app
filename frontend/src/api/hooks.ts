import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import { ctxToQueryString, type RangeCtx } from "./rangeContext";
import type {
  Agency,
  AskResponse,
  HeatmapCollection,
  LiveResponse,
  ReportMeta,
  ReportResponse,
  Route,
  TrendResponse,
} from "./types";

export function useRoutes(agencyId: number | null): UseQueryResult<Route[]> {
  return useQuery({
    queryKey: ["routes", agencyId],
    queryFn: () => apiGet<Route[]>(`/api/${agencyId}/routes`),
    enabled: agencyId != null,
    staleTime: 60 * 60 * 1000, // 1 hour: static data
  });
}

export function useAgencies(): UseQueryResult<Agency[]> {
  return useQuery({
    queryKey: ["agencies"],
    queryFn: () => apiGet<Agency[]>("/agencies"),
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

export function useReport(
  agencyId: number | null,
  reportType: string | null,
  ctx: RangeCtx,
): UseQueryResult<ReportResponse> {
  return useQuery({
    queryKey: ["reports", agencyId, reportType, ctx.from, ctx.to, ctx.dow, ctx.time_band],
    queryFn: () =>
      apiGet<ReportResponse>(
        `/api/${agencyId}/reports/${reportType}?${ctxToQueryString(ctx)}`,
      ),
    enabled: agencyId != null && !!reportType,
  });
}

export function useHeatmap(
  agencyId: number | null,
  ctx: RangeCtx,
): UseQueryResult<HeatmapCollection> {
  return useQuery({
    queryKey: ["heatmap", agencyId, ctx.from, ctx.to, ctx.dow, ctx.time_band],
    queryFn: () =>
      apiGet<HeatmapCollection>(
        `/api/${agencyId}/delays/heatmap?${ctxToQueryString(ctx)}`,
      ),
    enabled: agencyId != null,
    staleTime: 60 * 1000,
  });
}

export function useTrend(agencyId: number | null, ctx: RangeCtx): UseQueryResult<TrendResponse> {
  return useQuery({
    queryKey: ["trend", agencyId, ctx.from, ctx.to, ctx.dow, ctx.time_band],
    queryFn: () =>
      apiGet<TrendResponse>(`/api/${agencyId}/trend?${ctxToQueryString(ctx)}`),
    enabled: agencyId != null,
    staleTime: 60 * 1000,
  });
}

export function useLiveDelays(
  agencyId: number | null,
  options: { autoRefresh: boolean } = { autoRefresh: true },
): UseQueryResult<LiveResponse> {
  return useQuery({
    queryKey: ["live", agencyId],
    queryFn: () => apiGet<LiveResponse>(`/api/${agencyId}/delays/live`),
    enabled: agencyId != null,
    refetchInterval: options.autoRefresh ? 30_000 : false,
  });
}

export function useAsk(agencyId: number | null) {
  return useMutation({
    mutationFn: (vars: { question: string; ctx: RangeCtx }) => {
      if (agencyId == null) {
        return Promise.reject(new Error("事業者が選択されていません"));
      }
      return apiPost<AskResponse>(`/api/${agencyId}/ask`, {
        question: vars.question,
        ctx: {
          from: vars.ctx.from,
          to: vars.ctx.to,
          dow: vars.ctx.dow,
          time_band: vars.ctx.time_band,
        },
      });
    },
  });
}

export type CreateAgencyBody = Omit<Agency, "agency_id">;

export function useCreateAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAgencyBody) => apiPost<Agency>("/agencies", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}
