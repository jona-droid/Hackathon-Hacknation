import { useEffect, useMemo, useRef, useState } from "react";

export type SimEvent = { type: string; [k: string]: unknown };
export type SimState = {
  t: number;
  pos: number[];
  vel: number[];
  rpy: number[];
  quat: number[];
  cable_dist: number;
  collided: boolean;
  mode: string;
  inspected_count: number;
  violations?: Array<{ text: string }>;
};

export function useSimSocket() {
  const [state, setState] = useState<SimState | null>(null);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8000/ws");
    wsRef.current = ws;
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.type === "state") setState(data);
      if (data.type === "event") setEvents((prev) => [...prev.slice(-99), data]);
      if (data.type === "warning") setEvents((prev) => [...prev.slice(-99), data]);
    };
    return () => ws.close();
  }, []);

  const api = useMemo(
    () => ({
      state,
      events,
      sendKeys: (down: string[]) => {
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "keys", down }));
        }
      },
    }),
    [events, state]
  );

  return api;
}
