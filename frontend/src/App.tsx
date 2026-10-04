import { useEffect, useRef, useState } from "react";
import { FlightComparison } from "./FlightComparison";
import { Hud } from "./Hud";
import { KnowledgeViewer } from "./KnowledgeViewer";
import { MiniMap } from "./MiniMap";
import { Scene3D, SceneData } from "./Scene3D";
import { VoicePanel } from "./VoicePanel";
import { useSimSocket } from "./useSimSocket";
import { primeMicrophone } from "./recordAnswer";
import { API_URL } from "./config";

const FRAME_MAX_WIDTH = 768;

export function App() {
  const [mode, setMode] = useState<"expert" | "novice">("expert");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [scene, setScene] = useState<SceneData | null>(null);
  const [keysDown, setKeysDown] = useState<Set<string>>(new Set());
  const [inspected, setInspected] = useState<Set<string>>(new Set());
  const [warning, setWarning] = useState<string | null>(null);
  const [tab, setTab] = useState<"voice" | "knowledge" | "comparison">("voice");
  const [kbRefreshKey, setKbRefreshKey] = useState<number>(0);

  const { state, events, latestQuestion, latestAdvice, latestObservation, sendKeys, sendFrame } = useSimSocket();
  const lastFrameSendTime = useRef<number>(0);

  // Load 3D scene data (defects are re-drawn on every flight, so it is reloaded on Start)
  const loadScene = () =>
    fetch(`${API_URL}/scene`)
      .then((r) => r.json())
      .then(setScene)
      .catch((error) => console.warn("Backend unavailable: scene could not be loaded.", error));
  useEffect(() => {
    loadScene();
  }, []);

  // Track events for insulator inspections and alerts
  useEffect(() => {
    const e = events[events.length - 1];
    if (!e) return;
    if (e.type === "insulator_inspected" && e.insulator_id) {
      setInspected((prev) => new Set([...prev, String(e.insulator_id)]));
    }
    if (e.type === "very_close_cable") {
      setWarning(`Proximity Alert: ${e.cable_dist ?? 1.5}m from conductor!`);
    } else if (e.type === "collision") {
      setWarning(`COLLISION${e.with ? ` with ${String(e.with)}` : ""}! Motors stopped.`);
    }
  }, [events]);

  // Periodic camera frame capture from the 3D canvas, for the LLM observer and tutor.
  // state/sendFrame change at ~30 Hz, so read them through refs: depending on them would reset the timer forever.
  const latestState = useRef(state);
  latestState.current = state;
  const sendFrameRef = useRef(sendFrame);
  sendFrameRef.current = sendFrame;
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      if (now - lastFrameSendTime.current < 2000) return;
      lastFrameSendTime.current = now;

      const canvas = document.querySelector<HTMLCanvasElement>("section.left canvas");
      const s = latestState.current;
      if (!canvas || !s || !canvas.width) return;
      try {
        // Downscale before sending: smaller payload and fewer image tokens for Claude
        const scale = Math.min(1, FRAME_MAX_WIDTH / canvas.width);
        const small = document.createElement("canvas");
        small.width = Math.round(canvas.width * scale);
        small.height = Math.round(canvas.height * scale);
        small.getContext("2d")?.drawImage(canvas, 0, 0, small.width, small.height);
        sendFrameRef.current(small.toDataURL("image/jpeg", 0.7), s.t);
      } catch {
        // Canvas may be tainted or rendering
      }
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  // Refresh the knowledge tab when the apprentice learns or confirms a rule (confirmations happen without an answer)
  const kb = state?.knowledge;
  useEffect(() => {
    if (kb) setKbRefreshKey((k) => k + 1);
  }, [kb?.filled, kb?.confirmed]);

  // Session Start
  const startSession = async () => {
    // Ask for the microphone once here (a click is required); it is only switched on after each question.
    if (mode === "expert" && !(await primeMicrophone())) {
      alert("Microphone blocked: you can still type your answers. Allow the mic in the browser to answer by voice.");
    }
    try {
      const res = await fetch(`${API_URL}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const body = await res.json();
      setSessionId(body.session_id);
      setInspected(new Set());
      setWarning(null);
      loadScene();
    } catch (err) {
      console.error("Failed to start session:", err);
    }
  };

  // Session Stop
  const stopSession = async () => {
    try {
      const res = await fetch(`${API_URL}/session/stop`, { method: "POST" });
      if (!res.ok) return;
      const body = await res.json();
      setSessionId(body.session_id);
      setTab("comparison"); // Switch to comparison/summary tab upon completion
    } catch (err) {
      console.error("Failed to stop session:", err);
    }
  };

  // Keyboard controls synchronization
  useEffect(() => {
    sendKeys(Array.from(keysDown));
  }, [keysDown, sendKeys]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT") return;
      const key = e.key.toLowerCase();
      if (["arrowup", "arrowdown", "arrowleft", "arrowright", " ", "shift", "w", "s", "a", "d", "q", "e"].includes(key)) {
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

  // the backend is the source of truth: true from Start Flight until End Flight
  const sessionActive = !!state?.session_active;
  const pos = state?.pos ?? [-10, -10, 0];
  const yaw = state?.yaw ?? state?.rpy?.[2] ?? 0;

  return (
    <div className="app">
      <header>
        <div className="brand">
          <h2>⚡ Power Line AI Apprentice</h2>
          <span className="subbrand">Drone Maintenance &amp; Operator Knowledge Transfer</span>
        </div>

        <div className="mode-toggle">
          <label>Mode:</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as "expert" | "novice")}>
            <option value="expert">Senior Operator (AI Learns)</option>
            <option value="novice">Junior Operator (AI Coaches)</option>
          </select>
        </div>

        <div className="header-actions">
          <button className="btn-start" onClick={startSession}>
            ▶ Start Flight
          </button>
          <button className="btn-stop" onClick={stopSession} disabled={!sessionActive}>
            ⏹ End Flight
          </button>
        </div>

        <span className="controls-hint">
          W/S: fwd/back · A/D: strafe · Q/E: yaw · Space/Shift: climb/descend
        </span>
      </header>

      <main>
        {/* Left Section: 3D Simulation & Flight Gauges */}
        <section className="left" tabIndex={0}>
          <Scene3D scene={scene} pos={pos} yaw={yaw} inspected={inspected} />
          <Hud
            state={state}
            warning={warning}
            insulatorCount={scene?.insulators.length ?? 6}
            defectsFound={(state?.defects_spotted ?? []).map((id) => {
              const d = scene?.defects?.find((x) => x.id === id);
              return d ? `${d.label} (${d.target})` : id;
            })}
            mode={mode}
          />
          <MiniMap scene={scene} pos={pos} yaw={yaw} inspected={inspected} />
        </section>

        {/* Right Section: Interaction Tabs */}
        <section className="right">
          <div className="tabs">
            <button
              className={tab === "voice" ? "active" : ""}
              onClick={() => setTab("voice")}
            >
              🎙️ ElevenLabs Voice &amp; Dialogue
            </button>
            <button
              className={tab === "knowledge" ? "active" : ""}
              onClick={() => setTab("knowledge")}
            >
              📘 knowledge.md
            </button>
            <button
              className={tab === "comparison" ? "active" : ""}
              onClick={() => setTab("comparison")}
            >
              📊 Debrief &amp; Compare
            </button>
          </div>

          <div className="tab-content">
            {/* always mounted so End Flight can stop its audio/voice session even from another tab */}
            <VoicePanel
              mode={mode}
              sessionId={sessionId}
              simTime={state?.t ?? 0}
              events={events as any}
              latestQuestion={latestQuestion}
              latestAdvice={latestAdvice}
              latestObservation={latestObservation}
              sessionActive={sessionActive}
              hidden={tab !== "voice"}
              onKnowledgeUpdated={() => setKbRefreshKey((k) => k + 1)}
            />

            {tab === "knowledge" && <KnowledgeViewer refreshKey={kbRefreshKey} />}

            {tab === "comparison" && (
              <FlightComparison currentSessionId={sessionId} />
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

