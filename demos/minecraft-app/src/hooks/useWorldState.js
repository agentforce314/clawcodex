import { useState, useCallback, useRef, useEffect } from 'react';
import { generateChunkBlocks, getSpawnPosition, CHUNK_SIZE } from '../utils/terrain';
import { BlockType, INVENTORY_BLOCKS } from '../utils/blocks';

const RENDER_DISTANCE = 3;

function getChunkKey(cx, cz) {
  return `${cx},${cz}`;
}

export function useWorldState() {
  const [blocks, setBlocks] = useState({});
  const [selectedBlockIndex, setSelectedBlockIndex] = useState(0);
  const [spawnPos, setSpawnPos] = useState(null);
  const loadedChunks = useRef(new Set());

  const loadChunksAround = useCallback((playerX, playerZ) => {
    const cx = Math.floor(playerX / CHUNK_SIZE);
    const cz = Math.floor(playerZ / CHUNK_SIZE);
    let newBlocks = {};
    let anyNew = false;

    for (let dx = -RENDER_DISTANCE; dx <= RENDER_DISTANCE; dx++) {
      for (let dz = -RENDER_DISTANCE; dz <= RENDER_DISTANCE; dz++) {
        const key = getChunkKey(cx + dx, cz + dz);
        if (!loadedChunks.current.has(key)) {
          loadedChunks.current.add(key);
          const chunkBlocks = generateChunkBlocks(cx + dx, cz + dz);
          Object.assign(newBlocks, chunkBlocks);
          anyNew = true;
        }
      }
    }

    if (anyNew) {
      setBlocks(prev => ({ ...prev, ...newBlocks }));
    }
  }, []);

  // Initial world generation
  useEffect(() => {
    const initialBlocks = {};
    for (let dx = -RENDER_DISTANCE; dx <= RENDER_DISTANCE; dx++) {
      for (let dz = -RENDER_DISTANCE; dz <= RENDER_DISTANCE; dz++) {
        const key = getChunkKey(dx, dz);
        loadedChunks.current.add(key);
        const chunkBlocks = generateChunkBlocks(dx, dz);
        Object.assign(initialBlocks, chunkBlocks);
      }
    }
    setBlocks(initialBlocks);
    setSpawnPos(getSpawnPosition(initialBlocks));
  }, []);

  const addBlock = useCallback((x, y, z) => {
    const key = `${x},${y},${z}`;
    const blockType = INVENTORY_BLOCKS[selectedBlockIndex];
    setBlocks(prev => ({ ...prev, [key]: blockType }));
  }, [selectedBlockIndex]);

  const removeBlock = useCallback((x, y, z) => {
    const key = `${x},${y},${z}`;
    setBlocks(prev => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const selectBlock = useCallback((index) => {
    setSelectedBlockIndex(index);
  }, []);

  const cycleBlock = useCallback((direction) => {
    setSelectedBlockIndex(prev => {
      const next = prev + direction;
      if (next < 0) return INVENTORY_BLOCKS.length - 1;
      if (next >= INVENTORY_BLOCKS.length) return 0;
      return next;
    });
  }, []);

  return {
    blocks,
    spawnPos,
    selectedBlockIndex,
    selectedBlockType: INVENTORY_BLOCKS[selectedBlockIndex],
    addBlock,
    removeBlock,
    selectBlock,
    cycleBlock,
    loadChunksAround,
  };
}
