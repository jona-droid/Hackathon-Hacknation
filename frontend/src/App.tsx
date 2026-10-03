import { useEffect, useMemo, useState } from "react";
import { Hud } from "./Hud";
import { Scene3D } from "./Scene3D";
import { VoicePanel } from "./VoicePanel";
import { WorkMap } from "./WorkMap";
import { useSimSocket } from "./useSimSocket";

export function App() {
  const [mode, setMode] = useState<"expert" | "tutor" | "autonomous">("expert");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [scene, setScene] = useState<any>(null);
  const [keysDown, setKeysDown] = useState<Set<string>>(new Set());
  const [inspected, setInspected] = useState<Set<string>>(new Set());
  const [warning, setWarning] = useState<string | null>(null);
  const [tab, setTab] = useState<"voice" | "workmap" | "guardrails">("voice");
  const [guardrails, setGuardrails] = useState<Array<Record<string, unknown>>>([]);

  const { state, events, predictedPath, sendKeys } = useSimSocket();

  useEffect(() => {
    fetch("http://localhost:8000/scene").then((r) => r.json()).then(setScene);
    fetch("http://localhost:8000/guardrails").then((r) => r.json()).then((d) => setGuardrails(d.guardrails ?? []));
  }, []);

  useEffect(() => {
    const e = events[events.length - 1];
    if (!e) return;
    if (e.type === "insulator_inspected" && e.insulator_id) {
      setInspected((prev) => new Set([...prev, String(e.insulator_id)]));
    }
    if (e.type === "warning") setWarning(String(e.text ?? "Predicted violation"));
  }, [events]);

  const startSession = async () => {
    const res = await fetch("http://localhost:8000/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const body = await res.json();
    setSessionId(body.session_id);
    setInspected(new Set());
    setWarning(null);
  };

  const stopSession = async () => {
    const res = await fetch("http://localhost:8000/session/stop", { method: "POST" });
    const body = await res.json();
    setSessionId(body.session_id);
  };

  useEffect(() => {
    sendKeys(Array.from(keysDown));
  }, [keysDown, sendKeys]);

  const keyHandlers = useMemo(
    () => ({
      onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => setKeysDown((prev) => new Set([...prev, e.key])),
      onKeyUp: (e: React.KeyboardEvent<HTMLDivElement>) => setKeysDown((prev) => {
        const next = new Set(prev);
        next.delete(e.key);
        return next;
      }),
    }),
    []
  );

  const replay = async (t: number) => {
    if (!sessionId) return;
    const res = await fetch(`http://localhost:8000/session/${sessionId}/replay?t=${t}`);
    const body = await res.json();
    setWarning(`Replay @ ${body.t?.toFixed?.(1) ?? t}s`);
  };

  return (
    <div className="app">
      <header>
        <h2>Robot Apprentice</h2>
        <select value={mode} onChange={(e) => setMode(e.target.value as any)}>
          <option value="expert">Expert</option>
          <option value="tutor">Tutor</option>
          <option value="autonomous">Autonomous</option>
        </select>
        <button onClick={startSession}>Start</button>
        <button onClick={stopSession}>Stop</button>
        <button onClick={() => { setSessionId(null); setWarning(null); setInspected(new Set()); }}>Run demo</button>
      </header>
      <main>
        <section className="left" tabIndex={0} {...keyHandlers}>
          <Scene3D scene={scene} dronePos={state?.pos ?? [-10, -10, 0]} collided={!!state?.collided} predictedPath={predictedPath} inspected={inspected} />
          <Hud state={state} warning={warning} />
        </section>
        <section className="right">
          <div className="tabs">
            <button onClick={() => setTab("voice")}>Voice</button>
            <button onClick={() => setTab("workmap")}>Work Map</button>
            <button onClick={() => setTab("guardrails")}>Guardrails</button>
          </div>
          {tab === "voice" && (
            <VoicePanel
              mode={mode}
              sessionId={sessionId}
              simTime={state?.t ?? 0}
              events={events as any}
              guardrailContext={mode === "tutor" ? `GUARDRAILS=${JSON.stringify(guardrails)}` : undefined}
            />
          )}
          {tab === "workmap" && <WorkMap sessionId={sessionId} onReplay={replay} onGuardrailsSaved={setGuardrails} />}
          {tab === "guardrails" && <pre>{JSON.stringify(guardrails, null, 2)}</pre>}
        </section>
      </main>
    </div>
  );
}
