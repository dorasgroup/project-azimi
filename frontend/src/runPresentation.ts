/** Presentation only: terminal business status always wins over stale progress. */
export function runStageLabel(status: string, stage?: string | null): string | null {
  if (!["pending", "crawling", "processing", "analyzing"].includes(status)) return null;
  const labels: Record<string, string> = {
    distributed_crawling: "در حال خزش",
    distributed_analyzing: "در حال تحلیل منابع",
    distributed_aggregating: "در حال جمع‌بندی",
    distributed_reporting: "در حال تولید گزارش",
  };
  return stage ? labels[stage] ?? null : null;
}

export function responseClockOffset(dateHeader: string | null, receivedAt: number): number | null {
  if (!dateHeader) return null;
  const serverTime = Date.parse(dateHeader);
  return Number.isFinite(serverTime) ? serverTime - receivedAt : null;
}
