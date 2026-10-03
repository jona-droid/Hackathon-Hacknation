import { SimState } from "./useSimSocket";

export function Hud({
  state,
  warning,
  insulatorCount,
  mode,
}: {
  state: SimState | null;
  warning: string | null;
  insulatorCount: number;
  mode: string;
}) {
  const speed = state?.speed ?? 0;
  const accMag = state?.acc
    ? Math.hypot(state.acc[0] ?? 0, state.acc[1] ?? 0, state.acc[2] ?? 0)
    : 0;
  const heading = state?.rpy ? ((90 - (state.rpy[2] * 180) / Math.PI) % 360 + 360) % 360 : 0;
  const cableDist = state?.cable_dist ?? null;

  const isLowClearance = cableDist !== null && cableDist < 2.0;

  return (
    <div className="hud">
      <div className="hud-badge">{mode === "expert" ? "Expert Flight" : "Novice Training"}</div>
      <div className="hud-row">
        <span>Altitude:</span> <strong>{state?.altitude?.toFixed(1) ?? "0.0"} m</strong>
      </div>
      <div className="hud-row">
        <span>Speed:</span> <strong>{speed.toFixed(1)} m/s</strong>
      </div>
      <div className="hud-row">
        <span>Acceleration:</span> <strong>{accMag.toFixed(1)} m/s²</strong>
      </div>
      <div className="hud-row">
        <span>Heading:</span> <strong>{heading.toFixed(0)}°</strong>
      </div>
      <div className={`hud-row ${isLowClearance ? "danger" : ""}`}>
        <span>Cable Dist:</span>{" "}
        <strong>{cableDist !== null ? `${cableDist.toFixed(1)} m` : "-"}</strong>
      </div>
      <div className="hud-row">
        <span>Inspected:</span>{" "}
        <strong>
          {state?.inspected_count ?? 0}/{insulatorCount}
        </strong>
      </div>

      {warning && <div className="warning">{warning}</div>}
      {isLowClearance && <div className="warning">WARNING: Critical Cable Clearance (&lt; 2.0m)</div>}
      {state?.collided && <div className="warning">COLLISION DETECTED — Press Start to reset</div>}
    </div>
  );
}

