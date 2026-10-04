import { useEffect, useMemo, useRef, useState } from "react";
import { WS_URL } from "./config";

export type SimEvent = { type: string; [k: string]: unknown };

export type SimState = {
  t: number;
  pos: number[];
  vel: number[];
  acc: number[];
  speed: number;
  altitude: number;
  yaw: number;
  rpy: number[];
  quat: number[];
  cable_dist: number;
  nearest_cable_point: number[];
  collided: boolean;
  collision_with?: string | null;
  wind_speed?: number;
  wind_from_deg?: number;
  compass_interference?: number;
  position_hold?: boolean;
  defects_spotted?: string[];
  mode: string;
  inspected_count: number;
  violations?: Array<{ text: string }>;
  session_active?: boolean;
};

export type AIQuestion = {
  question: string;
  event: SimEvent;
  telemetry: Partial<SimState>;
  t: number;
};

export type AIObservation = { t: number; observation: string; asked: boolean };

export type AIAdvice = {
  speech: string;
  category: "safety_alert" | "technique_tip" | "qa_response";
  urgency: "low" | "medium" | "high";
  knowledge_reference: string;
};

export function useSimSocket() {
  const [state, setState] = useState<SimState | null>(null);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [latestQuestion, setLatestQuestion] = useState<AIQuestion | null>(null);
  const [latestAdvice, setLatestAdvice] = useState<AIAdvice | null>(null);
  const [latestObservation, setLatestObservation] = useState<AIObservation | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.type === "state") setState(data);
          if (data.type === "event") setEvents((prev) => [...prev.slice(-99), data]);
          if (data.type === "warning") setEvents((prev) => [...prev.slice(-99), data]);
          if (data.type === "question") setLatestQuestion(data);
          if (data.type === "advice") setLatestAdvice(data);
          if (data.type === "observation") setLatestObservation(data);
        } catch (e) {
          console.error("WS parse error:", e);
        }
      };

      ws.onclose = () => {
        reconnectTimeout = setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      clearTimeout(reconnectTimeout);
      ws?.close();
    };
  }, []);

  const api = useMemo(
    () => ({
      state,
      events,
      latestQuestion,
      latestAdvice,
      latestObservation,
      sendKeys: (down: string[]) => {
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "keys", down }));
        }
      },
      sendFrame: (frame_b64: string, t: number) => {
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "frame", frame_b64, t }));
        }
      },
    }),
    [events, state, latestQuestion, latestAdvice, latestObservation]
  );

  return api;
}

