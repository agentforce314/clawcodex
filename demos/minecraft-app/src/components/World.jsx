import { useMemo } from 'react';
import * as THREE from 'three';
import { BlockColors, BlockType } from '../utils/blocks';

const blockGeometry = new THREE.BoxGeometry(1, 1, 1);

// Create materials for each block type
const blockMaterials = {};
for (const [type, color] of Object.entries(BlockColors)) {
  const t = Number(type);
  if (t === BlockType.WATER) {
    blockMaterials[t] = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: 0.6,
    });
  } else if (t === BlockType.LEAVES) {
    blockMaterials[t] = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: 0.85,
    });
  } else if (t === BlockType.GRASS) {
    // Grass block: green top, brown sides/bottom
    blockMaterials[t] = [
      new THREE.MeshLambertMaterial({ color: '#6b5a2a' }), // right
      new THREE.MeshLambertMaterial({ color: '#6b5a2a' }), // left
      new THREE.MeshLambertMaterial({ color }), // top (green)
      new THREE.MeshLambertMaterial({ color: '#8b6914' }), // bottom (dirt)
      new THREE.MeshLambertMaterial({ color: '#6b5a2a' }), // front
      new THREE.MeshLambertMaterial({ color: '#6b5a2a' }), // back
    ];
  } else {
    blockMaterials[t] = new THREE.MeshLambertMaterial({ color });
  }
}

// Group blocks by type and create instanced meshes
function createInstancedMeshData(blocks) {
  const grouped = {};

  for (const [key, type] of Object.entries(blocks)) {
    if (type === BlockType.AIR) continue;
    if (!grouped[type]) grouped[type] = [];
    const [x, y, z] = key.split(',').map(Number);

    // Simple face culling: only render blocks with at least one exposed face
    const hasExposedFace =
      !blocks[`${x + 1},${y},${z}`] ||
      !blocks[`${x - 1},${y},${z}`] ||
      !blocks[`${x},${y + 1},${z}`] ||
      !blocks[`${x},${y - 1},${z}`] ||
      !blocks[`${x},${y},${z + 1}`] ||
      !blocks[`${x},${y},${z - 1}`];

    if (hasExposedFace) {
      grouped[type].push([x, y, z]);
    }
  }

  return grouped;
}

function BlockInstances({ type, positions }) {
  const mesh = useMemo(() => {
    const mat = blockMaterials[type];
    if (!mat) return null;

    // For grass with multi-material, use regular meshes approach via InstancedMesh per face isn't practical
    // So for grass we use a single green-brown material
    const material = Array.isArray(mat) ? mat : mat;
    const instancedMesh = new THREE.InstancedMesh(blockGeometry, material, positions.length);

    const dummy = new THREE.Object3D();
    for (let i = 0; i < positions.length; i++) {
      const [x, y, z] = positions[i];
      dummy.position.set(x + 0.5, y + 0.5, z + 0.5);
      dummy.updateMatrix();
      instancedMesh.setMatrixAt(i, dummy.matrix);
    }
    instancedMesh.instanceMatrix.needsUpdate = true;
    instancedMesh.castShadow = true;
    instancedMesh.receiveShadow = true;

    return instancedMesh;
  }, [type, positions]);

  if (!mesh) return null;
  return <primitive object={mesh} />;
}

export default function World({ blocks }) {
  const grouped = useMemo(() => createInstancedMeshData(blocks), [blocks]);

  return (
    <group>
      {Object.entries(grouped).map(([type, positions]) => (
        <BlockInstances key={type} type={Number(type)} positions={positions} />
      ))}
    </group>
  );
}
