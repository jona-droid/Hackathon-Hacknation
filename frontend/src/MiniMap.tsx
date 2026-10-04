import { SceneData } from "./Scene3D";
import { Prediction } from "./useSimSocket";

const X_MIN = -25;
const X_MAX = 145;
const Y_HALF = 25;

// Top-down tactical overview; SVG y grows downward, world y grows left of the line, so flip it.
export function MiniMap({
  scene,
  pos,
  yaw,
  inspected,
  trail,
  prediction,
}: {
  scene: SceneData | null;
  pos: number[];
  yaw: number;
  inspected: Set<string>;
  trail: number[][];
  prediction?: Prediction | null;
}) {
  if (!scene) return null;
  const deg = (-yaw * 180) / Math.PI;
  const risk = prediction?.risk ?? "none";
  const predColor = risk === "high" ? "#ff4d6a" : risk === "medium" ? "#ffb547" : "#3be8ff";
  return (
    <svg className="minimap" viewBox={`${X_MIN} ${-Y_HALF} ${X_MAX - X_MIN} ${2 * Y_HALF}`}>
      <defs>
        <pattern id="map-grid" width="10" height="10" patternUnits="userSpaceOnUse">
          <path d="M10 0 L0 0 0 10" fill="none" stroke="rgba(80,220,255,0.08)" strokeWidth={0.3} />
        </pattern>
      </defs>
      <rect x={X_MIN} y={-Y_HALF} width={X_MAX - X_MIN} height={2 * Y_HALF} fill="#04101c" />
      <rect x={X_MIN} y={-Y_HALF} width={X_MAX - X_MIN} height={2 * Y_HALF} fill="url(#map-grid)" />
      <rect x={scene.road_x[0]} y={-Y_HALF} width={scene.road_x[1] - scene.road_x[0]} height={2 * Y_HALF} fill="rgba(167,128,255,0.18)" />
      {scene.cables.map((pts, i) => (
        <line key={i} x1={pts[0][0]} y1={-pts[0][1]} x2={pts[pts.length - 1][0]} y2={-pts[0][1]} stroke="#ffb547" strokeWidth={0.5} opacity={0.8} />
      ))}
      {scene.pylons.map((p) => (
        <rect key={p.x} x={p.x - 1.5} y={-4.2} width={3} height={8.4} fill="none" stroke="#a3b6cf" strokeWidth={0.6} />
      ))}
      {scene.trees.map((t, i) => (
        <circle key={i} cx={t.center[0]} cy={-t.center[1]} r={scene.canopy_radius ?? 3} fill="rgba(61,255,162,0.22)" stroke="#3dffa2" strokeWidth={0.4} />
      ))}
      {scene.insulators.map((ins) => (
        <circle key={ins.id} cx={ins.pos[0]} cy={-ins.pos[1]} r={1.2} fill={inspected.has(ins.id) ? "#3dffa2" : "#4c8dff"} />
      ))}
      {/* flight path since take-off, starting point marked */}
      {trail.length > 1 && (
        <polyline
          points={trail.map((p) => `${p[0]},${-p[1]}`).join(" ")}
          fill="none"
          stroke="#ff8c00"
          strokeWidth={0.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity={0.85}
        />
      )}
      {trail.length > 0 && <circle cx={trail[0][0]} cy={-trail[0][1]} r={1.1} fill="#fff" stroke="#000" strokeWidth={0.3} />}
      {/* predicted course (next 3 s) */}
      {prediction?.path && prediction.path.length > 1 && (
        <polyline points={prediction.path.map((p) => `${p[0]},${-p[1]}`).join(" ")} fill="none" stroke={predColor} strokeWidth={0.7} strokeDasharray="1.6 1" />
      )}
      <g transform={`translate(${pos[0]} ${-pos[1]}) rotate(${deg})`}>
        <polygon points="3.2,0 -2,-2.2 -1,0 -2,2.2" fill="#3be8ff" stroke="#fff" strokeWidth={0.3} />
      </g>
    </svg>
  );
}
