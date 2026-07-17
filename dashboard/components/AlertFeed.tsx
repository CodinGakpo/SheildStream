"use client";

import type { AlertWithId } from "@/lib/useDashboardSocket";

function describe(alert: AlertWithId): string {
  if (alert.type === "THREAT_DETECTED") {
    return `${alert.rule} on ${alert.endpoint} (source ${alert.source.slice(0, 8)})`;
  }
  return `Anomalous traffic on ${alert.endpoint}: z=${alert.z_score}, ${alert.current_rps} rps`;
}

export function AlertFeed({ alerts }: { alerts: AlertWithId[] }) {
  return (
    <div className="flex h-full flex-col rounded-lg border border-zinc-800 bg-zinc-950 p-4">
      <h2 className="mb-2 text-sm font-medium text-zinc-400">Threat feed</h2>
      {alerts.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-zinc-600">
          No alerts yet
        </div>
      ) : (
        <ul className="flex-1 space-y-2 overflow-y-auto">
          {alerts.map((alert) => (
            <li
              key={alert._id}
              className="flex items-start gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm"
            >
              <span
                className={
                  "mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-xs font-semibold " +
                  (alert.severity === "HIGH"
                    ? "bg-red-950 text-red-400"
                    : "bg-amber-950 text-amber-400")
                }
              >
                {alert.severity}
              </span>
              <div className="min-w-0">
                <p className="truncate text-zinc-200">{describe(alert)}</p>
                {alert.count > 1 && (
                  <p className="text-xs text-zinc-500">×{alert.count} (deduplicated)</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
