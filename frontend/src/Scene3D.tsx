import { Line } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useRef } from "react";
import * as THREE from "three";

export type SceneData = {
  pylons: Array<{ x: number; height: number }>;
  cables: number[][][];
  road_x: number[];
  tree: { center: number[]; radius: number; height: number };
  insulators: Array<{ id: string; pos: number[] }>;
};

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

export function Scene3D(props: { scene: SceneData | null; pos: number[]; yaw: number; inspected: Set<string> }) {
  const { scene, pos, yaw, inspected } = props;
  return (
    <Canvas camera={{ fov: 80, near: 0.05, far: 600 }}>
      <color attach="background" args={["#9cc7e8"]} />
      <fog attach="fog" args={["#9cc7e8", 60, 320]} />
      <PovCamera pose={{ pos, yaw }} />
      <hemisphereLight args={["#dceeff", "#3b5d2e", 0.8]} />
      <directionalLight position={[40, -60, 80]} intensity={1.4} />
      <mesh position={[60, 0, 0]}>
        <planeGeometry args={[800, 800]} />
        <meshStandardMaterial color="#4f7d3a" />
      </mesh>
      <gridHelper args={[400, 80, "#3f6a2e", "#466f34"]} position={[60, 0, 0.02]} rotation={[Math.PI / 2, 0, 0]} />
      {scene && (
        <>
          <mesh position={[(scene.road_x[0] + scene.road_x[1]) / 2, 0, 0.04]}>
            <planeGeometry args={[scene.road_x[1] - scene.road_x[0], 800]} />
            <meshStandardMaterial color="#3a3a3a" />
          </mesh>
          {scene.pylons.map((p) => (
            <group key={p.x} position={[p.x, 0, 0]}>
              {[-0.9, 0.9].flatMap((x) =>
                [-1.8, 1.8].map((y) => (
                  <mesh key={`leg-${x}-${y}`} position={[x, y, p.height / 2]}>
                    <boxGeometry args={[0.45, 0.45, p.height]} />
                    <meshStandardMaterial color="#8d9398" />
                  </mesh>
                ))
              )}
              <mesh position={[0, 0, p.height / 2]}>
                <boxGeometry args={[0.35, 0.35, p.height]} />
                <meshStandardMaterial color="#737a80" />
              </mesh>
              <mesh position={[0, 0, p.height - 1.5]}>
                <boxGeometry args={[0.55, 7.2, 0.45]} />
                <meshStandardMaterial color="#737a80" />
              </mesh>
              <mesh position={[0, 0, p.height - 0.35]}>
                <boxGeometry args={[0.55, 4.8, 0.4]} />
                <meshStandardMaterial color="#737a80" />
              </mesh>
            </group>
          ))}
          {scene.cables.map((points, i) => (
            <Line key={i} points={points as [number, number, number][]} color="#111" lineWidth={2} />
          ))}
          {scene.insulators.map((ins) => (
            <mesh key={ins.id} position={ins.pos as [number, number, number]} rotation={[Math.PI / 2, 0, 0]}>
              <cylinderGeometry args={[0.3, 0.3, 1, 12]} />
              <meshStandardMaterial
                color={inspected.has(ins.id) ? "#2ecc71" : "#1e90ff"}
                emissive={inspected.has(ins.id) ? "#145a32" : "#0b3c6e"}
              />
            </mesh>
          ))}
          <mesh position={[scene.tree.center[0], scene.tree.center[1], scene.tree.height / 2]} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[scene.tree.radius, scene.tree.radius, scene.tree.height, 16]} />
            <meshStandardMaterial color="#5d4222" />
          </mesh>
          <mesh position={[scene.tree.center[0], scene.tree.center[1], scene.tree.height + 2]}>
            <sphereGeometry args={[5, 16, 16]} />
            <meshStandardMaterial color="forestgreen" />
          </mesh>
        </>
      )}
    </Canvas>
  );
}
