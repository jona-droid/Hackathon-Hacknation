import { SimState } from "./useSimSocket";

export function Hud({ state, warning, insulatorCount }: { state: SimState | null; warning: string | null; insulatorCount: number }) {
  const speed = state ? Math.hypot(state.vel?.[0] ?? 0, state.vel?.[1] ?? 0, state.vel?.[2] ?? 0) : 0;
  const heading = state?.rpy ? ((90 - (state.rpy[2] * 180) / Math.PI) % 360 + 360) % 360 : 0;
  return (
    <div className="hud">
      <div>Altitude: {state?.pos?.[2]?.toFixed(1) ?? "0.0"} m</div>
      <div>Speed: {speed.toFixed(1)} m/s</div>
      <div>Heading: {heading.toFixed(0)}°</div>
      <div>Cable dist: {state?.cable_dist?.toFixed(1) ?? "-"} m</div>
      <div>Inspected: {state?.inspected_count ?? 0}/{insulatorCount}</div>
      {warning && <div className="warning">{warning}</div>}
      {state?.collided && <div className="warning">COLLISION — press Start to reset</div>}
    </div>
  );
}
