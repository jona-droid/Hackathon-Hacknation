import { Radar } from "./Radar";
import { Ring } from "./Ring";
import { SceneData, Safety } from "./Scene3D";
import { Prediction, SimState } from "./useSimSocket";

const DEFAULT_SAFETY: Safety = {
  cable_danger_m: 2,
  cable_caution_m: 4,
  structure_danger_m: 1.5,
  structure_caution_m: 3,
  road_min_crossing_alt_m: 20,
};
const HAZARD_NAME: Record<string, string> = { cable: "CABLE", tower: "TOWER", tree: "TREE", ground: "GROUND" };

// ---- compass tape (heading 0 = +y = N, 90 = +x = E) ----
function Compass({ heading }: { heading: number }) {
  const PX_PER_DEG = 3;
  const ticks = [];
  for (let d = Math.floor((heading - 80) / 5) * 5; d <= heading + 80; d += 5) {
    const deg = ((d % 360) + 360) % 360;
    const x = (d - heading) * PX_PER_DEG;
    const major = deg % 15 === 0;
    ticks.push(<div key={`t${d}`} className="compass-tick" style={{ left: `calc(50% + ${x}px)`, height: major ? 10 : 5 }} />);
    if (deg % 30 === 0) {
      const name = { 0: "N", 90: "E", 180: "S", 270: "W" }[deg as 0 | 90 | 180 | 270];
      ticks.push(
        <div key={`l${d}`} className={`compass-label ${name ? "cardinal" : ""}`} style={{ left: `calc(50% + ${x}px)` }}>
          {name ?? deg}
        </div>
      );
    }
  }
  return (
    <div className="compass">
      <div className="compass-window">
        {ticks}
        <div className="compass-pointer" />
      </div>
      <div className="compass-value">{String(Math.round(heading) % 360).padStart(3, "0")}°</div>
    </div>
  );
}

// ---- vertical tape (speed / altitude) ----
type Marker = { value: number; kind: "cable" | "road"; label: string };
function Tape(props: {
  side: "left" | "right";
  caption: string;
  value: number;
  unit: string;
  span: number;
  step: number;
  labelEvery: number;
  min?: number;
  markers?: Marker[];
  footer: string;
  warn?: boolean;
}) {
  const { side, caption, value, unit, span, step, labelEvery, min = -Infinity, markers = [], footer, warn } = props;
  const H = 216;
  const k = H / span;
  const items = [];
  for (let v = Math.floor((value - span / 2) / step) * step; v <= value + span / 2; v += step) {
    if (v < min) continue;
    const y = H / 2 - (v - value) * k;
    const major = Math.abs(v % labelEvery) < 1e-6;
    items.push(<div key={`t${v}`} className="tape-tick" style={{ top: y, width: major ? 14 : 7 }} />);
    if (major) items.push(<div key={`n${v}`} className="tape-num" style={{ top: y }}>{v}</div>);
  }
  return (
    <div className={`tape tape-${side}`}>
      <div className="tape-caption top">{caption}</div>
      <div className="tape-window">
        {items}
        {markers.map((m) => {
          const y = H / 2 - (m.value - value) * k;
          if (y < 0 || y > H) return null;
          return (
            <div key={m.kind}>
              <div className={`tape-marker ${m.kind}`} style={{ top: y }} />
              <div className={`tape-marker-label ${m.kind}`} style={{ top: y }}>{m.label}</div>
            </div>
          );
        })}
      </div>
      <div className={`tape-value ${warn ? "warn" : ""}`}>
        {value.toFixed(1)}
        <small>{unit}</small>
      </div>
      <div className="tape-caption bottom">{footer}</div>
    </div>
  );
}

// ---- predictive alert ----
function alertFor(state: SimState | null, prediction?: Prediction | null) {
  if (!state || state.collided) return null;
  const g = state.guardian;
  if (g?.engaged) return { cls: "guardian", title: "Guardian engaged", detail: "AI is flying", action: g.action ?? "" };
  if (!prediction) return null;
  const c = prediction.conflict;
  if (prediction.risk === "high" && c)
    return { cls: "high", title: `Collision risk · ${HAZARD_NAME[c.hazard] ?? c.hazard}`, detail: `in ${c.in_s.toFixed(1)} s`, action: prediction.action_label ?? "BRAKE" };
  if (prediction.cannot_stop)
    return { cls: "medium", title: "Overspeed", detail: `stop distance ${prediction.stop_dist.toFixed(0)} m`, action: "RELEASE STICKS" };
  if (prediction.risk === "medium" && c)
    return { cls: "medium", title: `${HAZARD_NAME[c.hazard] ?? c.hazard} proximity`, detail: `in ${c.in_s.toFixed(1)} s`, action: prediction.action_label ?? "" };
  if (prediction.road_in_s !== undefined && prediction.road_in_s <= 4)
    return {
      cls: "road",
      title: "Road ahead",
      detail: `${prediction.road_in_s.toFixed(1)} s · ${prediction.road_alt?.toFixed(0)} m AGL`,
      action: prediction.action_label ?? "",
    };
  if ((state.compass_interference ?? 0) > 0.3) return { cls: "info", title: "Compass interference", detail: "heading unreliable", action: "" };
  return null;
}

function proxClass(d: number, danger: number, caution: number) {
  return d < danger ? "danger" : d < caution ? "caution" : "safe";
}

export function Hud({
  state,
  scene,
  mode,
  inspected,
  defectsFound,
  sessionActive,
}: {
  state: SimState | null;
  scene: SceneData | null;
  mode: "expert" | "novice";
  inspected: Set<string>;
  defectsFound: string[];
  sessionActive: boolean;
}) {
  const safety = scene?.safety ?? DEFAULT_SAFETY;
  const pos = state?.pos ?? [-10, -10, 0];
  const yaw = state?.yaw ?? 0;
  const heading = state?.heading_deg ?? ((90 - (yaw * 180) / Math.PI) % 360 + 360) % 360;
  const prediction = state?.prediction;
  const alert = alertFor(state, prediction);
  const kb = state?.knowledge;
  const windFrom = state?.wind_from_deg ?? 0;
  const compassBad = (state?.compass_interference ?? 0) > 0.3;
  const cableZ = state?.nearest_cable_point?.[2];
  const roadNear = (state?.road_dist ?? 99) < 30;

  const prox = [
    { name: "CABLE", d: state?.cable_dist ?? 99, danger: safety.cable_danger_m, caution: safety.cable_caution_m },
    { name: "TOWER", d: state?.pylon_dist ?? 99, danger: safety.structure_danger_m + 1, caution: safety.structure_caution_m + 3 },
    { name: "TREE", d: state?.tree_dist ?? 99, danger: safety.structure_danger_m, caution: safety.structure_caution_m },
    { name: "ROAD", d: state?.road_dist ?? 99, danger: -1, caution: 8 },
  ];

  return (
    <div className="hud-layer">
      <div className="vignette" />
      <div className="scanlines" />

      <Compass heading={heading} />
      {alert && (
        <div className={`alert-banner ${alert.cls}`}>
          <span>⚠ {alert.title}</span>
          <span className="alert-detail">{alert.detail}</span>
          {alert.action && <span className="alert-action">{alert.action}</span>}
        </div>
      )}

      {/* flight card: mode, task, knowledge */}
      <div className="hud-card flight-card">
        <div className="fc-row">
          <span className="eyebrow">{mode === "expert" ? "Expert · AI learns" : "Novice · AI coaches"}</span>
          {sessionActive && <span className="chip ok live" style={{ padding: "2px 8px" }}><span className="dot" />Live</span>}
        </div>
        <div>
          <div className="dim" style={{ fontSize: 10, letterSpacing: "0.16em", fontWeight: 700 }}>CURRENT TASK</div>
          <div className={`fc-task ${state?.task_name ? "" : "idle"}`}>{state?.task_name ?? "Standing by"}</div>
        </div>
        {kb && (
          <div className="ring-wrap">
            <Ring value={kb.filled} total={kb.total} secondary={kb.confirmed} />
            <div className="ring-label">
              <span>Knowledge</span>
              <strong>
                {kb.filled}/{kb.total}
              </strong>
              <em>{kb.confirmed} confirmed in flight</em>
            </div>
          </div>
        )}
      </div>

      <Tape
        side="left"
        caption="SPD"
        value={state?.speed ?? 0}
        unit="m/s"
        span={16}
        step={1}
        labelEvery={2}
        min={0}
        footer={`STOP ${(prediction?.stop_dist ?? 0).toFixed(0)} m`}
        warn={!!prediction?.cannot_stop}
      />
      <Tape
        side="right"
        caption="ALT"
        value={state?.altitude ?? 0}
        unit="m"
        span={30}
        step={1}
        labelEvery={5}
        min={0}
        markers={[
          ...(cableZ !== undefined && (state?.cable_dist ?? 99) < 25 ? [{ value: cableZ, kind: "cable" as const, label: "CABLE" }] : []),
          ...(roadNear ? [{ value: safety.road_min_crossing_alt_m, kind: "road" as const, label: "ROAD MIN" }] : []),
        ]}
        footer={`VS ${(state?.vertical_speed ?? 0) >= 0 ? "+" : ""}${(state?.vertical_speed ?? 0).toFixed(1)}`}
        warn={roadNear && (state?.altitude ?? 0) < safety.road_min_crossing_alt_m && (state?.road_dist ?? 99) < 8}
      />

      <div className="reticle">
        <div className="reticle-line l" />
        <div className="reticle-dot" />
        <div className="reticle-line r" />
      </div>

      {/* status */}
      <div className="hud-card status-card">
        <div className={`st-row ${compassBad ? "bad" : ""}`}>
          <span>Nav</span>
          <strong>{compassBad ? "COMPASS ERR" : state?.position_hold ? "GPS HOLD" : "GPS"}</strong>
        </div>
        <div className="st-row">
          <span>Wind</span>
          <strong>
            <span className="wind-arrow" style={{ transform: `rotate(${windFrom + 180 - heading}deg)` }}>↑</span>
            {(state?.wind_speed ?? 0).toFixed(1)} m/s · {windFrom.toFixed(0)}°
          </strong>
        </div>
        <div className="st-row">
          <span>Inspected</span>
          <div className="pips">
            {(scene?.insulators ?? []).map((ins) => (
              <div key={ins.id} className={`pip ${inspected.has(ins.id) ? "done" : ""}`}>{ins.id}</div>
            ))}
          </div>
        </div>
        <div className={`st-row ${defectsFound.length ? "warn" : ""}`}>
          <span>Defects</span>
          <strong>{defectsFound.length}</strong>
        </div>
        {defectsFound.slice(-2).map((d) => (
          <div key={d} className="defect-line">⚠ {d}</div>
        ))}
        {mode === "novice" ? (
          <div className={`st-row ${state?.guardian?.engaged ? "bad" : "ai"}`}>
            <span>Guardian</span>
            <strong>
              {!state?.guardian?.enabled ? "OFF" : state.guardian.engaged ? "ENGAGED" : "ARMED"} · {state?.guardian?.interventions ?? 0}
            </strong>
          </div>
        ) : (
          <div className="st-row ai">
            <span>AI usage</span>
            <strong>
              {state?.ai_usage?.calls ?? 0} calls · ${(state?.ai_usage?.cost_usd ?? 0).toFixed(3)}
            </strong>
          </div>
        )}
      </div>

      {/* sensors */}
      <div className="hud-card sensors">
        <Radar scene={scene} pos={pos} yaw={yaw} inspected={inspected} prediction={prediction} />
        <div className="prox">
          <div className="eyebrow" style={{ fontSize: 10 }}>Proximity</div>
          {prox.map((p) => {
            const fill = Math.max(4, Math.min(100, 100 * (1 - p.d / 20)));
            return (
              <div key={p.name} className={`prox-row ${proxClass(p.d, p.danger, p.caution)}`}>
                <span>{p.name}</span>
                <div className="prox-bar">
                  <div className="prox-fill" style={{ width: `${fill}%` }} />
                </div>
                <span className="prox-val">{p.d > 99 ? "—" : `${p.d.toFixed(1)}`}</span>
              </div>
            );
          })}
        </div>
      </div>

      {!sessionActive && !state?.collided && (
        <div className="standby">
          <h2>SYSTEMS READY</h2>
          <p>Press Start Flight to take off</p>
          <div className="keys-hint">
            <span><kbd>W</kbd><kbd>S</kbd> fwd / back</span>
            <span><kbd>A</kbd><kbd>D</kbd> strafe</span>
            <span><kbd>Q</kbd><kbd>E</kbd> yaw</span>
            <span><kbd>Space</kbd><kbd>Shift</kbd> climb / descend</span>
            <span><kbd>R</kbd> voice</span>
          </div>
        </div>
      )}

      {state?.collided && (
        <div className="crash-overlay">
          <div className="crash-box">
            <h2>{state.collision_with === "ground" ? "HARD LANDING" : `COLLISION · ${(state.collision_with ?? "").toUpperCase()}`}</h2>
            <p>Motors stopped — press Start Flight to reset</p>
          </div>
        </div>
      )}
    </div>
  );
}
