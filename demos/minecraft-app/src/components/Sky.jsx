import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export default function Sky() {
  const sunRef = useRef();

  useFrame(({ clock }) => {
    if (sunRef.current) {
      const t = clock.getElapsedTime() * 0.02;
      sunRef.current.position.set(
        Math.cos(t) * 200,
        Math.sin(t) * 150 + 100,
        Math.sin(t) * 200
      );
    }
  });

  return (
    <>
      {/* Sky dome color */}
      <color attach="background" args={['#87CEEB']} />
      <fog attach="fog" args={['#87CEEB', 60, 120]} />

      {/* Sun light */}
      <directionalLight
        ref={sunRef}
        intensity={1.2}
        color="#fff5e0"
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-camera-far={200}
        shadow-camera-left={-50}
        shadow-camera-right={50}
        shadow-camera-top={50}
        shadow-camera-bottom={-50}
      />

      {/* Ambient light for shadows */}
      <ambientLight intensity={0.4} color="#b4d7ff" />

      {/* Hemisphere light for natural sky coloring */}
      <hemisphereLight
        args={['#87CEEB', '#556b2f', 0.3]}
      />
    </>
  );
}
