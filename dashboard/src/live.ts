import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getToken } from "./api";

export interface LiveEvent {
  type: string;
  bot_id: string;
  ts: number;
  [k: string]: unknown;
}

/** Subscribe to /api/ws and invalidate the matching queries so every view stays live. */
export function useLiveEvents(onEvent?: (e: LiveEvent) => void): boolean {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 1000;
    let pending: number | null = null;
    const dirty = new Set<string>();

    const flush = () => {
      pending = null;
      for (const botId of dirty) {
        qc.invalidateQueries({ queryKey: ["bot", botId] });
      }
      dirty.clear();
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["bots"] });
    };

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const token = getToken();
      ws = new WebSocket(`${proto}://${location.host}/api/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`);
      ws.onopen = () => {
        setConnected(true);
        retry = 1000;
      };
      ws.onmessage = (msg) => {
        try {
          const e = JSON.parse(msg.data) as LiveEvent;
          cb.current?.(e);
          dirty.add(e.bot_id);
          if (pending === null) pending = window.setTimeout(flush, 750); // batch bursts (replays emit fast)
        } catch {
          /* ignore malformed */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          window.setTimeout(connect, retry);
          retry = Math.min(retry * 2, 15000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      if (pending !== null) window.clearTimeout(pending);
      ws?.close();
    };
  }, [qc]);
  return connected;
}
