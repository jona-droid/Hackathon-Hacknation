import { SimState } from "./useSimSocket";

export function Hud({ state, warning }: { state: SimState | null; warning: string | null }) {
  const speed = state ? Math.sqrt((state.vel?.[0] ?? 0) ** 2 + (state.vel?.[1] ?? 0) ** 2 + (state.vel?.[2] ?? 0) ** 2) : 0;
  return (
    <div className="hud">
      <div>Altitude: {state?.pos?.[2]?.toFixed(1) ?? "0.0"} m</div>
      <div>Speed: {speed.toFixed(1)} m/s</div>
      <div>Cable dist: {state?.cable_dist?.toFixed(1) ?? "-"} m</div>
      <div>Inspected: {state?.inspected_count ?? 0}/6</div>
      {warning && <div className="warning">{warning}</div>}
      {state?.collided && <div className="warning">COLLISION</div>}
    </div>
  );
}
