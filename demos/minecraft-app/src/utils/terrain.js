import { createNoise2D } from './noise';
import { BlockType } from './blocks';

const CHUNK_SIZE = 16;
const WORLD_HEIGHT = 32;
const SEA_LEVEL = 10;
const BASE_HEIGHT = 8;

const noise = createNoise2D(12345);
const treeNoise = createNoise2D(67890);

function getHeight(worldX, worldZ) {
  const scale1 = 0.02;
  const scale2 = 0.05;
  const scale3 = 0.1;

  const n1 = noise(worldX * scale1, worldZ * scale1) * 12;
  const n2 = noise(worldX * scale2, worldZ * scale2) * 6;
  const n3 = noise(worldX * scale3, worldZ * scale3) * 3;

  return Math.floor(BASE_HEIGHT + n1 + n2 + n3);
}

function shouldPlaceTree(worldX, worldZ, height) {
  if (height <= SEA_LEVEL) return false;
  const v = treeNoise(worldX * 0.5, worldZ * 0.5);
  return v > 0.85;
}

export function generateChunkBlocks(chunkX, chunkZ) {
  const blocks = {};
  const trees = [];

  for (let x = 0; x < CHUNK_SIZE; x++) {
    for (let z = 0; z < CHUNK_SIZE; z++) {
      const worldX = chunkX * CHUNK_SIZE + x;
      const worldZ = chunkZ * CHUNK_SIZE + z;
      const height = getHeight(worldX, worldZ);

      for (let y = 0; y < WORLD_HEIGHT; y++) {
        let blockType = BlockType.AIR;

        if (y === 0) {
          blockType = BlockType.STONE;
        } else if (y < height - 3) {
          blockType = BlockType.STONE;
        } else if (y < height) {
          blockType = BlockType.DIRT;
        } else if (y === height) {
          if (height <= SEA_LEVEL) {
            blockType = BlockType.SAND;
          } else {
            blockType = BlockType.GRASS;
          }
        } else if (y <= SEA_LEVEL && y > height) {
          blockType = BlockType.WATER;
        }

        if (blockType !== BlockType.AIR) {
          const key = `${worldX},${y},${worldZ}`;
          blocks[key] = blockType;
        }
      }

      if (shouldPlaceTree(worldX, worldZ, height) && height > SEA_LEVEL) {
        trees.push({ x: worldX, z: worldZ, baseY: height + 1 });
      }
    }
  }

  // Place trees
  for (const tree of trees) {
    const trunkHeight = 4 + Math.floor(Math.abs(treeNoise(tree.x * 0.3, tree.z * 0.3)) * 3);
    // Trunk
    for (let y = 0; y < trunkHeight; y++) {
      const key = `${tree.x},${tree.baseY + y},${tree.z}`;
      blocks[key] = BlockType.WOOD;
    }
    // Leaves canopy
    const leafBase = tree.baseY + trunkHeight - 2;
    for (let dy = 0; dy < 4; dy++) {
      const radius = dy < 3 ? 2 : 1;
      for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
          if (dx === 0 && dz === 0 && dy < 2) continue; // trunk space
          if (Math.abs(dx) === radius && Math.abs(dz) === radius && Math.random() > 0.6) continue;
          const key = `${tree.x + dx},${leafBase + dy},${tree.z + dz}`;
          if (!blocks[key]) {
            blocks[key] = BlockType.LEAVES;
          }
        }
      }
    }
  }

  return blocks;
}

export function getSpawnPosition(worldBlocks) {
  // Find a safe spawn point near origin
  for (let x = 0; x < 5; x++) {
    for (let z = 0; z < 5; z++) {
      const height = getHeight(x, z);
      if (height > SEA_LEVEL) {
        return [x + 0.5, height + 2, z + 0.5];
      }
    }
  }
  return [8, 20, 8];
}

export { CHUNK_SIZE, WORLD_HEIGHT };
