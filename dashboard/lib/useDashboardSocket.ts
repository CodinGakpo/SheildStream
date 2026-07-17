"use client";

import { useEffect, useRef, useState } from "react";
import type { AlertMessage, DashboardMessage } from "./types";
import { isAlertMessage } from "./types";

const RPS_HISTORY_LIMIT = 60;
const ALERT_FEED_LIMIT = 50;
const BACKOFF_INITIAL_MS = 1000;
const BACKOFF_MAX_MS = 30000;

export type RpsPoint = { ts: number; rps: number };
export type AlertWithId = AlertMessage & { _id: number };

export type ConnectionStatus = "connecting" | "open" | "reconnecting";

export function useDashboardSocket(url: string) {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [rpsHistory, setRpsHistory] = useState<RpsPoint[]>([]);
  const [alerts, setAlerts] = useState<AlertWithId[]>([]);

  // Refs, not state: these drive reconnect timing/socket lifecycle and must
  // never trigger a re-render or be recreated across renders.
  const backoffMsRef = useRef(BACKOFF_INITIAL_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const alertSeqRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        backoffMsRef.current = BACKOFF_INITIAL_MS; // reset backoff on success
        setStatus("open");
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        let msg: DashboardMessage;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return; // malformed frame — drop, don't crash the client
        }

        if (msg.type === "METRIC_SNAPSHOT") {
          setRpsHistory((prev) => {
            const next = [...prev, { ts: msg.ts, rps: msg.rps }];
            return next.length > RPS_HISTORY_LIMIT
              ? next.slice(next.length - RPS_HISTORY_LIMIT)
              : next;
          });
        } else if (isAlertMessage(msg)) {
          alertSeqRef.current += 1;
          const withId: AlertWithId = { ...msg, _id: alertSeqRef.current };
          setAlerts((prev) => {
            const next = [withId, ...prev];
            return next.length > ALERT_FEED_LIMIT ? next.slice(0, ALERT_FEED_LIMIT) : next;
          });
        }
      };

      socket.onclose = () => {
        socketRef.current = null;
        if (cancelled) return;
        setStatus("reconnecting");
        const delay = backoffMsRef.current;
        backoffMsRef.current = Math.min(delay * 2, BACKOFF_MAX_MS);
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      // No separate onerror handling: a WS error is always followed by
      // onclose (per spec), which already owns the reconnect logic.
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
    };
  }, [url]);

  return { status, rpsHistory, alerts };
}
