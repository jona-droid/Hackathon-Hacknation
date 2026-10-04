import { SceneData } from "./Scene3D";
import { Prediction } from "./useSimSocket";

const R = 80; // svg radius (px)
const RANGE_M = 35;

/** Drone-centred proximity radar, heading up: cables, towers, trees, road and the predicted path. */
export function Radar({
  scene,
  pos,
  yaw,
  inspected,
  prediction,
}: {
  scene: SceneData | null;
  pos: number[];
  yaw: number;
  inspected: Set<string>;
  prediction?: Prediction | null;
}) {
  const k = R / RANGE_M;
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  // world -> screen: forward is up, left is left
  const proj = (x: number, y: number): [number, number] => {
    const dx = x - pos[0];
    const dy = y - pos[1];
    const fwd = dx * cos + dy * sin;
    const left = -dx * sin + dy * cos;
    return [R - left * k, R - fwd * k];
  };
  const line = (pts: number[][]) => pts.map((p) => proj(p[0], p[1]).map((v) => v.toFixed(1)).join(",")).join(" ");
  const risk = prediction?.risk ?? "none";
  const pathColor = risk === "high" ? "#ff4d6a" : risk === "medium" ? "#ffb547" : "#3be8ff";

  return (
    <svg className="radar" width={2 * R} height={2 * R} viewBox={`0 0 ${2 * R} ${2 * R}`}>
      <defs>
        <clipPath id="radar-clip">
          <circle cx={R} cy={R} r={R - 1} />
        </clipPath>
        <radialGradient id="radar-bg">
          <stop offset="0%" stopColor="rgba(10,40,60,0.85)" />
          <stop offset="100%" stopColor="rgba(2,8,16,0.9)" />
        </radialGradient>
        <linearGradient id="radar-sweep" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgba(59,232,255,0)" />
          <stop offset="100%" stopColor="rgba(59,232,255,0.35)" />
        </linearGradient>
      </defs>
      <circle cx={R} cy={R} r={R - 1} fill="url(#radar-bg)" stroke="rgba(80,220,255,0.35)" />
      <g clipPath="url(#radar-clip)">
        {scene && (
          <>
            {/* road */}
            <polygon
              points={line([
                [scene.road_x[0], -400],
                [scene.road_x[1], -400],
                [scene.road_x[1], 400],
                [scene.road_x[0], 400],
              ])}
              fill="rgba(167,128,255,0.18)"
              stroke="rgba(167,128,255,0.5)"
              strokeWidth={0.8}
            />
            {/* cables */}
            {scene.cables.map((pts, i) => (
              <polyline key={i} points={line(pts)} fill="none" stroke="#ffb547" strokeWidth={1.4} opacity={0.85} />
            ))}
            {/* towers */}
            {scene.pylons.map((p) => {
              const [sx, sy] = proj(p.x, 0);
              return <rect key={p.x} x={sx - 4} y={sy - 4} width={8} height={8} fill="#a3b6cf" stroke="#e8f3ff" strokeWidth={0.8} transform={`rotate(${(-yaw * 180) / Math.PI} ${sx} ${sy})`} />;
            })}
            {/* trees */}
            {scene.trees.map((t, i) => {
              const [sx, sy] = proj(t.center[0], t.center[1]);
              return <circle key={i} cx={sx} cy={sy} r={(scene.canopy_radius ?? 3) * k} fill="rgba(61,255,162,0.25)" stroke="#3dffa2" strokeWidth={1} />;
            })}
            {/* insulators */}
            {scene.insulators.map((ins) => {
              const [sx, sy] = proj(ins.pos[0], ins.pos[1]);
              return <circle key={ins.id} cx={sx} cy={sy} r={2.6} fill={inspected.has(ins.id) ? "#3dffa2" : "#4c8dff"} />;
            })}
          </>
        )}
        {/* predicted path */}
        {prediction?.path && prediction.path.length > 1 && (
          <polyline points={line(prediction.path)} fill="none" stroke={pathColor} strokeWidth={2} strokeDasharray="4 3" />
        )}
        {prediction?.stop_point && (() => {
          const [sx, sy] = proj(prediction.stop_point[0], prediction.stop_point[1]);
          return <circle cx={sx} cy={sy} r={4} fill="none" stroke={pathColor} strokeWidth={1.5} />;
        })()}
        {/* range rings and sweep */}
        {[1, 2, 3].map((i) => (
          <circle key={i} cx={R} cy={R} r={(R * i) / 3.5} fill="none" stroke="rgba(80,220,255,0.15)" />
        ))}
        <line x1={R} y1={0} x2={R} y2={2 * R} stroke="rgba(80,220,255,0.12)" />
        <line x1={0} y1={R} x2={2 * R} y2={R} stroke="rgba(80,220,255,0.12)" />
        <path className="radar-sweep" d={`M${R},${R} L${R},0 A${R},${R} 0 0 1 ${R + R * Math.sin(Math.PI / 5)},${R - R * Math.cos(Math.PI / 5)} Z`} fill="url(#radar-sweep)" />
      </g>
      {/* the drone */}
      <polygon points={`${R},${R - 7} ${R - 5},${R + 5} ${R},${R + 2} ${R + 5},${R + 5}`} fill="#3be8ff" stroke="#fff" strokeWidth={0.6} />
      <text x={R} y={2 * R - 6} textAnchor="middle" fontSize={8} fill="#5f7591" fontFamily="JetBrains Mono">
        {RANGE_M} m
      </text>
    </svg>
  );
}
