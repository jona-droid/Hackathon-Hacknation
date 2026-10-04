import { SceneData } from "./Scene3D";

const X_MIN = -25;
const X_MAX = 145;
const Y_HALF = 25;

// Top-down overview; SVG y grows downward, world y grows left of the line, so flip it.
export function MiniMap({ scene, pos, yaw, inspected, trail }: { scene: SceneData | null; pos: number[]; yaw: number; inspected: Set<string>; trail: number[][] }) {
  if (!scene) return null;
  const deg = (-yaw * 180) / Math.PI;
  return (
    <svg className="minimap" viewBox={`${X_MIN} ${-Y_HALF} ${X_MAX - X_MIN} ${2 * Y_HALF}`}>
      <rect x={X_MIN} y={-Y_HALF} width={X_MAX - X_MIN} height={2 * Y_HALF} fill="#2f5524" />
      <rect x={scene.road_x[0]} y={-Y_HALF} width={scene.road_x[1] - scene.road_x[0]} height={2 * Y_HALF} fill="#444" />
      {scene.cables.map((pts, i) => (
        <line key={i} x1={pts[0][0]} y1={-pts[0][1]} x2={pts[pts.length - 1][0]} y2={-pts[0][1]} stroke="#111" strokeWidth={0.6} />
      ))}
      {scene.pylons.map((p) => (
        <rect key={p.x} x={p.x - 1.2} y={-3.6} width={2.4} height={7.2} fill="#9aa1a6" />
      ))}
      {scene.insulators.map((ins) => (
        <circle key={ins.id} cx={ins.pos[0]} cy={-ins.pos[1]} r={1.1} fill={inspected.has(ins.id) ? "#2ecc71" : "#1e90ff"} />
      ))}
      {scene.trees.map((t, i) => (
        <circle key={i} cx={t.center[0]} cy={-t.center[1]} r={3} fill="forestgreen" />
      ))}
      {/* flight path since take-off, starting point marked */}
      {trail.length > 1 && (
        <polyline
          points={trail.map((p) => `${p[0]},${-p[1]}`).join(" ")}
          fill="none"
          stroke="#ffb347"
          strokeWidth={0.6}
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity={0.9}
        />
      )}
      {trail.length > 0 && <circle cx={trail[0][0]} cy={-trail[0][1]} r={1.2} fill="#fff" stroke="#000" strokeWidth={0.3} />}
      <g transform={`translate(${pos[0]} ${-pos[1]}) rotate(${deg})`}>
        <polygon points="3,0 -2,-2 -2,2" fill="orange" stroke="#000" strokeWidth={0.3} />
      </g>
    </svg>
  );
}
