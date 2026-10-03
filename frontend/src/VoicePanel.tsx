import { useEffect, useMemo, useRef, useState } from "react";
import { useConversation } from "@elevenlabs/react";

type Row = { role: string; text: string; t: number };

const FORWARD_TYPES = new Set(["hover_start", "very_close_cable", "over_road", "near_tree", "insulator_inspected", "land", "collision"]);

export function VoicePanel(props: {
  mode: "expert" | "tutor" | "autonomous";
  sessionId: string | null;
  simTime: number;
  events: Array<{ type: string; [k: string]: unknown }>;
  guardrailContext?: string;
}) {
  const { mode, sessionId, simTime, events, guardrailContext } = props;
  const agentId = mode === "tutor" ? import.meta.env.VITE_ELEVENLABS_TUTOR_AGENT_ID : import.meta.env.VITE_ELEVENLABS_AGENT_ID;
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState("idle");
  const lastForwardRef = useRef(0);

  const conversation: any = useConversation({
    onMessage: async (m: any) => {
      const role = m?.source ?? "agent";
      const text = m?.message ?? "";
      const row = { role, text, t: simTime };
      setRows((r) => [...r.slice(-80), row]);
      if (sessionId && text) {
        await fetch(`http://localhost:8000/session/${sessionId}/transcript`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: role === "user" ? "user" : "agent", text, t: simTime }),
        });
      }
    },
    onStatusChange: (s: string) => setStatus(s),
  });

  useEffect(() => {
    if (!agentId || !conversation?.startSession) return;
    (async () => {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      await conversation.startSession({ agentId });
      if (mode === "tutor" && guardrailContext) {
        await conversation.sendContextualUpdate?.(guardrailContext);
      }
    })();
    return () => {
      conversation.endSession?.();
    };
  }, [agentId, conversation, mode, guardrailContext]);

  useEffect(() => {
    const ev = events[events.length - 1];
    if (!ev || !FORWARD_TYPES.has(ev.type)) return;
    const now = Date.now();
    if (ev.type !== "land" && ev.type !== "collision" && now - lastForwardRef.current < 3000) return;
    lastForwardRef.current = now;
    const msg = `[EVENT t=${simTime.toFixed(1)}s] ${ev.type}`;
    conversation.sendContextualUpdate?.(msg);
  }, [events, simTime, conversation]);

  const transcript = useMemo(() => rows.slice(-12), [rows]);

  return (
    <div>
      <div>Status: {status}</div>
      <div className="transcript">
        {transcript.map((r, i) => (
          <div key={i}>
            <strong>{r.role}:</strong> {r.text}
          </div>
        ))}
      </div>
    </div>
  );
}
