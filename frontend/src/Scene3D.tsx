import { Html, Line } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { memo, useMemo, useRef } from "react";
import * as THREE from "three";
import { Prediction } from "./useSimSocket";

/** Safety margins shared with the backend (scene_json), so the HUD colours match the alarms. */
export type Safety = {
  cable_danger_m: number;
  cable_caution_m: number;
  structure_danger_m: number;
  structure_caution_m: number;
  road_min_crossing_alt_m: number;
};

export type SceneData = {
  pylons: Array<{ x: number; height: number }>;
  cables: number[][][];
  road_x: number[];
  trees: Array<{ center: number[]; radius: number; height: number }>;
  insulators: Array<{ id: string; pos: number[] }>;
  tower?: Tower;
  canopy_radius?: number;
  defects?: Defect[];
  safety?: Safety;
};

export type Defect = {
  id: string;
  kind: "broken_disc" | "flashover" | "pollution" | "bird_nest" | "broken_strands" | "foreign_object";
  component: "insulator" | "cable";
  label: string;
  target: string;
  pos: number[];
  disc?: number;
  seed?: number;
};

type Tower = {
  base_half_width: number;
  top_half_width: number;
  crossarm_z: number;
  crossarm_half_span: number;
  peak_z: number;
};

const DEFAULT_TOWER: Tower = { base_half_width: 2, top_half_width: 0.7, crossarm_z: 27, crossarm_half_span: 4.2, peak_z: 30 };
const STEEL = new THREE.MeshStandardMaterial({ color: "#8d9398", metalness: 0.6, roughness: 0.45 });
const BRACING_LEVELS = 7;
const INSULATOR_DISCS = 9;

type V3 = [number, number, number];
type Beam = { a: V3; b: V3; w: number };

// A straight steel member between two points (box stretched along a -> b)
function BeamMesh({ a, b, w }: Beam) {
  const { position, quaternion, length } = useMemo(() => {
    const va = new THREE.Vector3(...a);
    const vb = new THREE.Vector3(...b);
    const dir = vb.clone().sub(va);
    return {
      position: va.clone().add(vb).multiplyScalar(0.5),
      quaternion: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir.clone().normalize()),
      length: dir.length(),
    };
  }, [a, b]);
  return (
    <mesh position={position} quaternion={quaternion} material={STEEL} castShadow>
      <boxGeometry args={[w, w, length]} />
    </mesh>
  );
}

// Tapered lattice tower: 4 legs, X-bracing on every face, a lattice crossarm and an earth-wire peak.
// Same footprint as the collision volume in backend/sim/scene.py.
function towerBeams(t: Tower): Beam[] {
  const beams: Beam[] = [];
  const hw = (z: number) => t.base_half_width - ((t.base_half_width - t.top_half_width) * z) / t.crossarm_z;
  const corners = (z: number): V3[] => {
    const h = hw(z);
    return [[h, h, z], [-h, h, z], [-h, -h, z], [h, -h, z]];
  };
  const levels = Array.from({ length: BRACING_LEVELS + 1 }, (_, i) => (t.crossarm_z * i) / BRACING_LEVELS);
  for (let c = 0; c < 4; c++) beams.push({ a: corners(0)[c], b: corners(t.crossarm_z)[c], w: 0.28 });
  for (let i = 0; i < BRACING_LEVELS; i++) {
    const lo = corners(levels[i]);
    const hi = corners(levels[i + 1]);
    for (let c = 0; c < 4; c++) {
      const n = (c + 1) % 4;
      beams.push({ a: lo[c], b: hi[n], w: 0.1 }, { a: lo[n], b: hi[c], w: 0.1 }, { a: hi[c], b: hi[n], w: 0.12 });
    }
  }
  // crossarm: two chords along y with diagonal web
  const z = t.crossarm_z;
  const s = t.crossarm_half_span;
  for (const dx of [-0.35, 0.35]) {
    beams.push({ a: [dx, -s, z], b: [dx, s, z], w: 0.2 });
    beams.push({ a: [dx, -s, z], b: [dx, -t.top_half_width, z + 1.2], w: 0.12 });
    beams.push({ a: [dx, s, z], b: [dx, t.top_half_width, z + 1.2], w: 0.12 });
  }
  // peak carrying the earth wire
  for (const [cx, cy] of [[1, 1], [-1, 1], [-1, -1], [1, -1]]) {
    beams.push({ a: [cx * t.top_half_width, cy * t.top_half_width, z], b: [0, 0, t.peak_z], w: 0.14 });
  }
  return beams;
}

// Small deterministic PRNG so a defect looks the same on every render
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const Y_UP = new THREE.Vector3(0, 1, 0);

// Thin rods sticking out of a point in random directions (frayed strands, nest twigs)
function Spikes({ seed, count, minLen, maxLen, radius, color, flatX = false }: { seed: number; count: number; minLen: number; maxLen: number; radius: number; color: string; flatX?: boolean }) {
  const rods = useMemo(() => {
    const r = rng(seed);
    return Array.from({ length: count }, () => {
      const dir = new THREE.Vector3(flatX ? (r() - 0.5) * 0.6 : r() - 0.5, r() - 0.5, r() - 0.5).normalize();
      const l = minLen + r() * (maxLen - minLen);
      return {
        position: dir.clone().multiplyScalar(l / 2),
        quaternion: new THREE.Quaternion().setFromUnitVectors(Y_UP, dir),
        l,
      };
    });
  }, [seed, count, minLen, maxLen, flatX]);
  return (
    <>
      {rods.map((rod, i) => (
        <mesh key={i} position={rod.position} quaternion={rod.quaternion}>
          <cylinderGeometry args={[radius, radius * 0.5, rod.l, 5]} />
          <meshStandardMaterial color={color} metalness={flatX ? 0.8 : 0} roughness={flatX ? 0.35 : 1} />
        </mesh>
      ))}
    </>
  );
}

// String of glass/porcelain discs hanging from the crossarm down to the cable clamp
function InsulatorString({ pos, topZ, inspected, defect }: { pos: number[]; topZ: number; inspected: boolean; defect?: Defect }) {
  const [x, y, z] = pos;
  const len = topZ - z;
  const color = inspected ? "#2ecc71" : "#1e90ff";
  const emissive = inspected ? "#145a32" : "#0b3c6e";
  const first = defect?.disc ?? 0;
  const discLook = (i: number) => {
    if (defect?.kind === "pollution") return { color: "#8a7350", emissive: "#241a0c", roughness: 1 };
    if (defect?.kind === "flashover" && i >= first && i <= first + 2) return { color: "#1f1f1f", emissive: "#000000", roughness: 0.9 };
    return { color, emissive, roughness: 0.25 };
  };
  return (
    <group position={[x, y, 0]}>
      <mesh position={[0, 0, z + len / 2]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.04, 0.04, len, 6]} />
        <meshStandardMaterial color="#555" metalness={0.7} />
      </mesh>
      {Array.from({ length: INSULATOR_DISCS }, (_, i) => {
        const look = discLook(i);
        const broken = defect?.kind === "broken_disc" && i === first;
        return (
          <mesh key={i} position={[0, 0, z + 0.25 + (i * (len - 0.4)) / (INSULATOR_DISCS - 1)]} rotation={[Math.PI / 2, 0, 0]} castShadow>
            {/* a shattered disc keeps only part of its skirt */}
            <cylinderGeometry args={broken ? [0.24, 0.24, 0.07, 16, 1, false, 0.6, 3.4] : [0.24, 0.24, 0.07, 16]} />
            <meshStandardMaterial color={look.color} emissive={look.emissive} roughness={look.roughness} side={THREE.DoubleSide} />
          </mesh>
        );
      })}
    </group>
  );
}

function BirdNest({ pos, seed }: { pos: number[]; seed: number }) {
  return (
    <group position={pos as V3}>
      <mesh scale={[1, 1, 0.45]} castShadow>
        <icosahedronGeometry args={[0.42, 1]} />
        <meshStandardMaterial color="#6b4a2b" roughness={1} flatShading />
      </mesh>
      <Spikes seed={seed} count={14} minLen={0.5} maxLen={0.9} radius={0.018} color="#4a321c" />
    </group>
  );
}

// Damage hanging on a conductor: frayed strands (birdcaging) or a tangled kite
function CableDefect({ defect }: { defect: Defect }) {
  const seed = defect.seed ?? 1;
  const [x, y, z] = defect.pos;
  if (defect.kind === "broken_strands") {
    return (
      <group position={[x, y, z]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.07, 0.07, 0.5, 10]} />
          <meshStandardMaterial color="#9a9ea3" metalness={0.8} roughness={0.4} />
        </mesh>
        <Spikes seed={seed} count={12} minLen={0.25} maxLen={0.7} radius={0.02} color="#d3d6da" flatX />
      </group>
    );
  }
  const r = rng(seed);
  const tilt = (r() - 0.5) * 0.8;
  const tail: V3[] = Array.from({ length: 12 }, (_, i) => [Math.sin(i * 1.3 + seed) * 0.15, 0, -1.25 - i * 0.14]);
  return (
    <group position={[x, y, z]}>
      <Line points={[[0, 0, 0], [0.1, 0, -0.5]]} color="#eeeeee" lineWidth={1} />
      <group position={[0.1, 0, -0.85]} rotation={[0, tilt, 0]}>
        <mesh rotation={[Math.PI / 2, 0, Math.PI / 4]}>
          <planeGeometry args={[0.55, 0.55]} />
          <meshStandardMaterial color="#d62828" side={THREE.DoubleSide} />
        </mesh>
        <Line points={[[0, 0, 0.39], [0, 0, -0.39]]} color="#f6c90e" lineWidth={2} />
        <Line points={[[-0.39, 0, 0], [0.39, 0, 0]]} color="#f6c90e" lineWidth={2} />
        <Line points={[[0, 0, -0.39], ...tail.map(([tx, ty, tz]) => [tx, ty, tz + 0.85] as V3)]} color="#f6c90e" lineWidth={2} />
      </group>
    </group>
  );
}

type Pose = { pos: number[]; yaw: number };

const CAMERA_DOWN_TILT = THREE.MathUtils.degToRad(5);
const Z_AXIS = new THREE.Vector3(0, 0, 1);
const Y_AXIS = new THREE.Vector3(0, 1, 0);
// three.js cameras look down -Z with +Y up; the drone body is x forward, y left, z up.
const CAMERA_MOUNT = new THREE.Quaternion().setFromRotationMatrix(
  new THREE.Matrix4().makeBasis(new THREE.Vector3(0, -1, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(-1, 0, 0))
);

// Gimbal-stabilised FPV camera: follows drone position and heading, horizon stays level.
function PovCamera({ pose }: { pose: Pose }) {
  const poseRef = useRef(pose);
  poseRef.current = pose;
  const target = useRef({ pos: new THREE.Vector3(), quat: new THREE.Quaternion() });
  const initialised = useRef(false);

  useFrame(({ camera }, dt) => {
    const { pos, yaw } = poseRef.current;
    const t = target.current;
    t.pos.set(pos[0] + 0.15 * Math.cos(yaw), pos[1] + 0.15 * Math.sin(yaw), pos[2] + 0.05);
    t.quat
      .setFromAxisAngle(Z_AXIS, yaw)
      .multiply(new THREE.Quaternion().setFromAxisAngle(Y_AXIS, CAMERA_DOWN_TILT))
      .multiply(CAMERA_MOUNT);
    if (!initialised.current) {
      camera.position.copy(t.pos);
      camera.quaternion.copy(t.quat);
      initialised.current = true;
      return;
    }
    // state arrives at ~30 Hz; smooth toward it every rendered frame
    const k = 1 - Math.exp(-dt * 18);
    camera.position.lerp(t.pos, k);
    camera.quaternion.slerp(t.quat, k);
  });
  return null;
}

// Sun: low in the sky on the side the light comes from (SUN_LIGHT below), in view from the take-off
// point. Unlit and unfogged: it stays bright at any distance (the camera far plane is 600 m).
const SUN_POS: V3 = [424, -210, 155];
const SUN_LIGHT: V3 = [87, -50, 100]; // same direction as the sun, higher: shorter shadows

function Sun() {
  return (
    <group position={SUN_POS}>
      <mesh>
        <sphereGeometry args={[14, 32, 16]} />
        <meshBasicMaterial color="#fff6d5" fog={false} toneMapped={false} />
      </mesh>
      <mesh>
        <sphereGeometry args={[26, 32, 16]} />
        <meshBasicMaterial color="#ffe9a8" transparent opacity={0.25} depthWrite={false} fog={false} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Tree({ center, radius, height, canopy }: { center: number[]; radius: number; height: number; canopy: number }) {
  return (
    <>
      <mesh position={[center[0], center[1], height / 2]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[radius, radius, height, 16]} />
        <meshStandardMaterial color="#5d4222" roughness={0.9} />
      </mesh>
      <mesh position={[center[0], center[1], height + 2]} castShadow>
        <icosahedronGeometry args={[canopy, 2]} />
        <meshStandardMaterial color="forestgreen" roughness={0.95} flatShading />
      </mesh>
    </>
  );
}

// Static scene (towers, cables, defects, trees): memoised so 30 Hz pose updates don't re-render it
const World = memo(function World({ scene, inspected }: { scene: SceneData; inspected: Set<string> }) {
  const tower = scene.tower ?? DEFAULT_TOWER;
  const beams = useMemo(() => towerBeams(tower), [tower]);
  return (
    <>
      <mesh position={[(scene.road_x[0] + scene.road_x[1]) / 2, 0, 0.04]}>
        <planeGeometry args={[scene.road_x[1] - scene.road_x[0], 800]} />
        <meshStandardMaterial color="#3a3a3a" />
      </mesh>
      {scene.pylons.map((p) => (
        <group key={p.x} position={[p.x, 0, 0]}>
          {beams.map((b, k) => (
            <BeamMesh key={k} {...b} />
          ))}
        </group>
      ))}
      {scene.cables.map((points, i) => (
        <Line key={i} points={points as [number, number, number][]} color="#111" lineWidth={2} />
      ))}
      {scene.insulators.map((ins) => (
        <InsulatorString
          key={ins.id}
          pos={ins.pos}
          topZ={tower.crossarm_z}
          inspected={inspected.has(ins.id)}
          defect={scene.defects?.find((d) => d.target === ins.id && d.kind !== "bird_nest")}
        />
      ))}
      {scene.defects?.map((d) =>
        d.kind === "bird_nest" ? (
          <BirdNest key={d.id} pos={d.pos} seed={Number(d.id.slice(1)) * 97} />
        ) : d.component === "cable" ? (
          <CableDefect key={d.id} defect={d} />
        ) : null
      )}
      {scene.trees.map((t, i) => (
        <Tree key={i} {...t} canopy={scene.canopy_radius ?? 3} />
      ))}
    </>
  );
});

const RISK_COLOR: Record<string, string> = { none: "#3be8ff", low: "#7fe9ff", medium: "#ffb547", high: "#ff4d6a" };

/** The pilot's course over the next 3 s (model-predictive safety), dashes flowing forward. */
function PredictedCourse({ prediction }: { prediction: Prediction }) {
  const ref = useRef<any>(null);
  useFrame((_, dt) => {
    const mat = ref.current?.material;
    if (mat) mat.dashOffset -= dt * 2.5;
  });
  const color = RISK_COLOR[prediction.risk] ?? RISK_COLOR.none;
  const stop = prediction.stop_point;
  return (
    <>
      {prediction.path.length > 1 && (
        <Line ref={ref} points={prediction.path as V3[]} color={color} lineWidth={3} dashed dashSize={0.9} gapSize={0.5} transparent opacity={0.9} />
      )}
      {stop && (
        <mesh position={stop as V3}>
          <torusGeometry args={[0.7, 0.06, 8, 32]} />
          <meshBasicMaterial color={color} transparent opacity={0.85} toneMapped={false} />
        </mesh>
      )}
    </>
  );
}

/** Augmented-reality tags floating over the insulators: id, status and distance from the drone. */
function InsulatorTags({ insulators, inspected, pos }: { insulators: SceneData["insulators"]; inspected: Set<string>; pos: number[] }) {
  return (
    <>
      {insulators.map((ins) => {
        const d = Math.hypot(ins.pos[0] - pos[0], ins.pos[1] - pos[1], ins.pos[2] - pos[2]);
        if (d > 90) return null;
        const done = inspected.has(ins.id);
        return (
          <Html key={ins.id} position={[ins.pos[0], ins.pos[1], ins.pos[2] + 3.6]} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
            <div className={`ar-tag ${done ? "done" : ""} ${d < 8 ? "near" : ""}`}>
              <b>{ins.id.toUpperCase()}</b>
              <span>{done ? "✓ INSPECTED" : "PENDING"}</span>
              <em>{d.toFixed(0)} m</em>
            </div>
          </Html>
        );
      })}
    </>
  );
}

/** Pulsing marker on the closest point of the nearest cable when the drone is near it. */
function CableThreat({ point, dist, caution }: { point: number[]; dist: number; caution: number }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (ref.current) ref.current.scale.setScalar(1 + 0.35 * Math.sin(clock.elapsedTime * 8));
  });
  if (dist >= caution) return null;
  return (
    <mesh ref={ref} position={point as V3}>
      <sphereGeometry args={[0.35, 16, 12]} />
      <meshBasicMaterial color={dist < caution / 2 ? "#ff4d6a" : "#ffb547"} transparent opacity={0.85} toneMapped={false} />
    </mesh>
  );
}

export function Scene3D(props: {
  scene: SceneData | null;
  pos: number[];
  yaw: number;
  inspected: Set<string>;
  trail: number[][];
  prediction?: Prediction | null;
  cablePoint?: number[];
  cableDist?: number;
}) {
  const { scene, pos, yaw, inspected, trail, prediction, cablePoint, cableDist } = props;
  // preserveDrawingBuffer: lets App capture camera frames with toDataURL (blank otherwise)
  return (
    <Canvas shadows camera={{ fov: 80, near: 0.05, far: 600 }} gl={{ preserveDrawingBuffer: true }}>
      <color attach="background" args={["#9cc7e8"]} />
      <fog attach="fog" args={["#9cc7e8", 60, 320]} />
      <PovCamera pose={{ pos, yaw }} />
      <hemisphereLight args={["#dceeff", "#3b5d2e", 0.8]} />
      <Sun />
      <directionalLight
        position={SUN_LIGHT}
        intensity={1.4}
        castShadow
        shadow-mapSize={[4096, 4096]}
        shadow-bias={-0.0005}
        shadow-camera-left={-170}
        shadow-camera-right={170}
        shadow-camera-top={170}
        shadow-camera-bottom={-170}
        shadow-camera-far={400}
      />
      <mesh position={[60, 0, 0]} receiveShadow>
        <planeGeometry args={[800, 800]} />
        <meshStandardMaterial color="#4f7d3a" />
      </mesh>
      <gridHelper args={[400, 80, "#3f6a2e", "#466f34"]} position={[60, 0, 0.02]} rotation={[Math.PI / 2, 0, 0]} />
      {scene && <World scene={scene} inspected={inspected} />}
      {/* flight path since take-off */}
      {trail.length > 1 && <Line points={trail as V3[]} color="#ff8c00" lineWidth={2.5} transparent opacity={0.85} />}
      {prediction && <PredictedCourse prediction={prediction} />}
      {scene && <InsulatorTags insulators={scene.insulators} inspected={inspected} pos={pos} />}
      {cablePoint && cableDist !== undefined && <CableThreat point={cablePoint} dist={cableDist} caution={scene?.safety?.cable_caution_m ?? 4} />}
    </Canvas>
  );
}
