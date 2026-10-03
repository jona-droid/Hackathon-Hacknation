import { useEffect, useState } from "react";
import { Hud } from "./Hud";
import { MiniMap } from "./MiniMap";
import { Scene3D, SceneData } from "./Scene3D";
import { VoicePanel } from "./VoicePanel";
import { WorkMap } from "./WorkMap";
import { useSimSocket } from "./useSimSocket";

export function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [scene, setScene] = useState<SceneData | null>(null);
  const [keysDown, setKeysDown] = useState<Set<string>>(new Set());
  const [inspected, setInspected] = useState<Set<string>>(new Set());
  const [warning, setWarning] = useState<string | null>(null);
  const [tab, setTab] = useState<"voice" | "workmap" | "guardrails">("voice");
  const [guardrails, setGuardrails] = useState<Array<Record<string, unknown>>>([]);
  const [expertQuestions, setExpertQuestions] = useState<string[]>([]);

  const { state, events, sendKeys } = useSimSocket();

  useEffect(() => {
    fetch("http://localhost:8000/scene")
      .then((r) => r.json())
      .then(setScene)
      .catch((error) => console.warn("Backend unavailable: scene could not be loaded.", error));
    fetch("http://localhost:8000/guardrails")
      .then((r) => r.json())
      .then((d) => setGuardrails(d.guardrails ?? []))
      .catch((error) => console.warn("Backend unavailable: guardrails could not be loaded.", error));
    fetch("http://localhost:8000/expert/questions")
      .then((r) => r.json())
      .then((d) => setExpertQuestions(d.questions ?? []))
      .catch((error) => console.warn("Backend unavailable: expert questions could not be loaded.", error));
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
      body: JSON.stringify({ mode: "expert" }),
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

  useEffect(() => {
    // lower-case so Shift+W and a later plain "w" keyup refer to the same key
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT") return;
      const key = e.key.toLowerCase();
      if (["arrowup", "arrowdown", "arrowleft", "arrowright", " ", "shift"].includes(key)) {
        e.preventDefault();
      }
      setKeysDown((prev) => {
        if (prev.has(key)) return prev;
        return new Set([...prev, key]);
      });
    };
    const onKeyUp = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      setKeysDown((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    };
    const onBlur = () => setKeysDown(new Set());
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  const pos = state?.pos ?? [-10, -10, 0];
  const yaw = state?.rpy?.[2] ?? 0;

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
        <span className="mode">Expert flight</span>
        <button onClick={startSession}>Start</button>
        <button onClick={stopSession}>Stop</button>
        <button onClick={() => { setSessionId(null); setWarning(null); setInspected(new Set()); }}>Run demo</button>
        <span className="controls">W/S or ↑/↓: forward/back · A/D: strafe · Q/E or ←/→: turn · Space/Shift: up/down</span>
      </header>
      <main>
        <section className="left" tabIndex={0}>
          <Scene3D scene={scene} pos={pos} yaw={yaw} inspected={inspected} />
          <Hud state={state} warning={warning} insulatorCount={scene?.insulators.length ?? 0} />
          <MiniMap scene={scene} pos={pos} yaw={yaw} inspected={inspected} />
        </section>
        <section className="right">
          <div className="tabs">
            <button onClick={() => setTab("voice")}>Voice</button>
            <button onClick={() => setTab("workmap")}>Work Map</button>
            <button onClick={() => setTab("guardrails")}>Guardrails</button>
          </div>
          {tab === "voice" && (
            <VoicePanel
              mode="expert"
              sessionId={sessionId}
              simTime={state?.t ?? 0}
              events={events as any}
              expertQuestions={expertQuestions}
              guardrailContext={undefined}
            />
          )}
          {tab === "workmap" && <WorkMap sessionId={sessionId} onReplay={replay} onGuardrailsSaved={setGuardrails} />}
          {tab === "guardrails" && <pre>{JSON.stringify(guardrails, null, 2)}</pre>}
        </section>
      </main>
    </div>
  );
}
