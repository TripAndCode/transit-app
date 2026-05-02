import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  Agency,
  AskResponse,
  HeatmapCollection,
  LiveDelay,
  ReportMeta,
  ReportResponse,
} from "./types";

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
): UseQueryResult<ReportResponse> {
  return useQuery({
    queryKey: ["report", agencyId, reportType],
    queryFn: () =>
      apiGet<ReportResponse>(`/api/${agencyId}/reports/${reportType}`),
    enabled: agencyId != null && !!reportType,
  });
}

export function useHeatmap(agencyId: number | null): UseQueryResult<HeatmapCollection> {
  return useQuery({
    queryKey: ["heatmap", agencyId],
    queryFn: () => apiGet<HeatmapCollection>(`/api/${agencyId}/delays/heatmap`),
    enabled: agencyId != null,
    staleTime: 5 * 60 * 1000,
  });
}

export function useLiveDelays(
  agencyId: number | null,
  options: { autoRefresh: boolean } = { autoRefresh: true },
): UseQueryResult<LiveDelay[]> {
  return useQuery({
    queryKey: ["live", agencyId],
    queryFn: () => apiGet<LiveDelay[]>(`/api/${agencyId}/delays/live`),
    enabled: agencyId != null,
    refetchInterval: options.autoRefresh ? 30_000 : false,
  });
}

export function useAsk(agencyId: number | null) {
  return useMutation({
    mutationFn: (question: string) =>
      apiPost<AskResponse>(`/api/${agencyId}/ask`, { question }),
  });
}

export function useCreateAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { agency_name: string; feed_url: string; static_url?: string | null }) =>
      apiPost<Agency>("/agencies", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}
