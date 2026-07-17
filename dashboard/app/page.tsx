"use client";

import { RpsChart } from "@/components/RpsChart";
import { AlertFeed } from "@/components/AlertFeed";
import { useDashboardSocket } from "@/lib/useDashboardSocket";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/dashboard";

export default function Home() {
  const { status, rpsHistory, alerts } = useDashboardSocket(WS_URL);

  return (
    <div className="flex flex-1 flex-col gap-4 bg-zinc-900 p-4 sm:p-8">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-zinc-100">ShieldStream Dashboard</h1>
        <span
          data-testid="connection-status"
          data-status={status}
          className={
            "rounded-full px-3 py-1 text-xs font-medium " +
            (status === "open"
              ? "bg-emerald-950 text-emerald-400"
              : "bg-zinc-800 text-zinc-400")
          }
        >
          {status === "open" ? "● live" : status === "reconnecting" ? "○ reconnecting…" : "○ connecting…"}
        </span>
      </header>

      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <RpsChart data={rpsHistory} />
        </div>
        <div className="lg:col-span-1 lg:row-span-1">
          <AlertFeed alerts={alerts} />
        </div>
      </div>
    </div>
  );
}
