"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { RpsPoint } from "@/lib/useDashboardSocket";

export function RpsChart({ data }: { data: RpsPoint[] }) {
  const chartData = data.map((point) => ({
    ...point,
    label: new Date(point.ts * 1000).toLocaleTimeString(),
  }));

  return (
    <div className="h-64 w-full rounded-lg border border-zinc-800 bg-zinc-950 p-4">
      <h2 className="mb-2 text-sm font-medium text-zinc-400">Requests / sec</h2>
      {chartData.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-sm text-zinc-600">
          Waiting for first metric snapshot…
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="90%">
          <LineChart data={chartData}>
            <XAxis dataKey="label" hide />
            <YAxis width={32} stroke="#71717a" fontSize={12} allowDecimals={false} />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
              labelStyle={{ color: "#a1a1aa" }}
            />
            <Line
              type="monotone"
              dataKey="rps"
              stroke="#22d3ee"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
