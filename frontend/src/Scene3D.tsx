import { Line, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";

type SceneData = {
  pylons: Array<{ x: number; height: number }>;
  cables: number[][][];
  road_x: number[];
  tree: { center: number[]; radius: number; height: number };
  insulators: Array<{ id: string; pos: number[] }>;
};

export function Scene3D(props: {
  scene: SceneData | null;
  dronePos: number[];
  collided: boolean;
  predictedPath: number[][];
  inspected: Set<string>;
}) {
  const { scene, dronePos, collided, predictedPath, inspected } = props;
  return (
    <Canvas camera={{ position: [15, -20, 20], fov: 55 }}>
      <ambientLight intensity={0.9} />
      <directionalLight position={[10, -10, 20]} intensity={1.2} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[60, 0, 0]}>
        <planeGeometry args={[220, 80]} />
        <meshStandardMaterial color="#2f6d39" />
      </mesh>
      {scene && (
        <>
          <mesh position={[(scene.road_x[0] + scene.road_x[1]) / 2, 0, 0.01]} rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[scene.road_x[1] - scene.road_x[0], 80]} />
            <meshStandardMaterial color="#333" />
          </mesh>
          {scene.pylons.map((p) => (
            <mesh key={p.x} position={[p.x, 0, p.height / 2]}>
              <boxGeometry args={[1, 1, p.height]} />
              <meshStandardMaterial color="#888" />
            </mesh>
          ))}
          {scene.cables.map((points, i) => (
            <Line key={i} points={points as any} color="black" lineWidth={2} />
          ))}
          {scene.insulators.map((ins) => (
            <mesh key={ins.id} position={ins.pos as any}>
              <cylinderGeometry args={[0.3, 0.3, 1, 12]} />
              <meshStandardMaterial color={inspected.has(ins.id) ? "green" : "dodgerblue"} />
            </mesh>
          ))}
          <mesh position={[scene.tree.center[0], scene.tree.center[1], scene.tree.height / 2]}>
            <cylinderGeometry args={[scene.tree.radius, scene.tree.radius, scene.tree.height, 16]} />
            <meshStandardMaterial color="#5d4222" />
          </mesh>
          <mesh position={[scene.tree.center[0], scene.tree.center[1], scene.tree.height + 2]}>
            <sphereGeometry args={[3, 16, 16]} />
            <meshStandardMaterial color="forestgreen" />
          </mesh>
        </>
      )}
      {predictedPath.length > 1 && <Line points={predictedPath as any} color="red" dashed dashScale={2} />}
      <mesh position={dronePos as any}>
        <boxGeometry args={[0.9, 0.9, 0.3]} />
        <meshStandardMaterial color={collided ? "red" : "orange"} />
      </mesh>
      <OrbitControls />
    </Canvas>
  );
}
