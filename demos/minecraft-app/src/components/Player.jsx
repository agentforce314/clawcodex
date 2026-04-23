import { useRef, useEffect, useCallback } from 'react';
import { useThree, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const MOVE_SPEED = 8;
const SWIM_SPEED = 4;
const JUMP_FORCE = 8;
const SWIM_UP_FORCE = 4;
const GRAVITY = -20;
const WATER_GRAVITY = -4;
const PLAYER_HEIGHT = 1.6;
const PLAYER_RADIUS = 0.3;
const MOUSE_SENSITIVITY = 0.002;
const REACH_DISTANCE = 6;

// Block breaking times in seconds (by block type)
const BREAK_TIMES = {
  1: 0.6,   // grass
  2: 0.5,   // dirt
  3: 1.5,   // stone
  4: 1.0,   // wood
  5: 0.3,   // leaves
  6: 0.5,   // sand
  8: 1.5,   // cobblestone
  9: 0.8,   // planks
  10: 1.5,  // brick
  11: 0.4,  // snow
};

function raycastBlock(camera, blocks) {
  const dir = new THREE.Vector3();
  camera.getWorldDirection(dir);
  const origin = camera.position.clone();
  const step = 0.05;
  let prevPos = null;

  for (let d = 0; d < REACH_DISTANCE; d += step) {
    const x = origin.x + dir.x * d;
    const y = origin.y + dir.y * d;
    const z = origin.z + dir.z * d;
    const bx = Math.floor(x);
    const by = Math.floor(y);
    const bz = Math.floor(z);
    const key = `${bx},${by},${bz}`;

    if (blocks[key] && blocks[key] !== 7) {
      return { hit: [bx, by, bz], prev: prevPos, type: blocks[key] };
    }
    prevPos = [bx, by, bz];
  }
  return null;
}

export default function Player({ spawnPos, blocks, onAddBlock, onRemoveBlock, onLoadChunks, miningState }) {
  const { camera, gl } = useThree();
  const velocity = useRef(new THREE.Vector3());
  const keys = useRef({});
  const isLocked = useRef(false);
  const euler = useRef(new THREE.Euler(0, 0, 0, 'YXZ'));
  const lastChunkCheck = useRef({ x: 0, z: 0 });
  const isGrounded = useRef(false);
  const isInWater = useRef(false);

  // Mining state
  const leftMouseDown = useRef(false);
  const miningTarget = useRef(null);  // { x, y, z, type }
  const miningProgress = useRef(0);

  // Set initial position
  useEffect(() => {
    if (spawnPos) {
      camera.position.set(spawnPos[0], spawnPos[1], spawnPos[2]);
      euler.current.set(0, 0, 0);
    }
  }, [spawnPos, camera]);

  const getBlockAt = useCallback((x, y, z) => {
    const bx = Math.floor(x);
    const by = Math.floor(y);
    const bz = Math.floor(z);
    const key = `${bx},${by},${bz}`;
    return blocks[key] || 0;
  }, [blocks]);

  const isBlockSolid = useCallback((x, y, z) => {
    const block = getBlockAt(x, y, z);
    return block !== 0 && block !== 7; // not air and not water
  }, [getBlockAt]);

  const isWater = useCallback((x, y, z) => {
    return getBlockAt(x, y, z) === 7;
  }, [getBlockAt]);

  // Pointer lock
  useEffect(() => {
    const canvas = gl.domElement;

    const onClick = () => {
      if (!isLocked.current) {
        canvas.requestPointerLock();
      }
    };

    const onLockChange = () => {
      isLocked.current = document.pointerLockElement === canvas;
    };

    const onMouseMove = (e) => {
      if (!isLocked.current) return;
      euler.current.y -= e.movementX * MOUSE_SENSITIVITY;
      euler.current.x -= e.movementY * MOUSE_SENSITIVITY;
      euler.current.x = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, euler.current.x));
      camera.quaternion.setFromEuler(euler.current);
    };

    const onKeyDown = (e) => {
      keys.current[e.code] = true;
    };

    const onKeyUp = (e) => {
      keys.current[e.code] = false;
    };

    canvas.addEventListener('click', onClick);
    document.addEventListener('pointerlockchange', onLockChange);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('keyup', onKeyUp);

    return () => {
      canvas.removeEventListener('click', onClick);
      document.removeEventListener('pointerlockchange', onLockChange);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('keyup', onKeyUp);
    };
  }, [camera, gl]);

  // Mouse button tracking + right-click block placement
  useEffect(() => {
    const onMouseDown = (e) => {
      if (!isLocked.current) return;
      if (e.button === 0) {
        leftMouseDown.current = true;
      } else if (e.button === 2) {
        const result = raycastBlock(camera, blocks);
        if (result && result.prev) {
          onAddBlock(result.prev[0], result.prev[1], result.prev[2]);
        }
      }
    };

    const onMouseUp = (e) => {
      if (e.button === 0) {
        leftMouseDown.current = false;
        miningTarget.current = null;
        miningProgress.current = 0;
        miningState.current = { progress: 0, target: null };
      }
    };

    const onContextMenu = (e) => e.preventDefault();

    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('contextmenu', onContextMenu);

    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('contextmenu', onContextMenu);
    };
  }, [camera, blocks, onAddBlock, miningState]);

  useFrame((_, delta) => {
    if (!isLocked.current) return;

    const dt = Math.min(delta, 0.05);
    const pos = camera.position.clone();

    // --- Mining logic ---
    if (leftMouseDown.current) {
      const result = raycastBlock(camera, blocks);
      if (result) {
        const [bx, by, bz] = result.hit;
        const target = miningTarget.current;

        // Check if we're still looking at the same block
        if (target && target.x === bx && target.y === by && target.z === bz) {
          const breakTime = BREAK_TIMES[result.type] || 1.0;
          miningProgress.current += dt;
          const progress = Math.min(miningProgress.current / breakTime, 1);
          miningState.current = { progress, target: [bx, by, bz] };

          if (miningProgress.current >= breakTime) {
            onRemoveBlock(bx, by, bz);
            miningTarget.current = null;
            miningProgress.current = 0;
            miningState.current = { progress: 0, target: null };
            leftMouseDown.current = false;
          }
        } else {
          // Started looking at a different block, reset
          miningTarget.current = { x: bx, y: by, z: bz, type: result.type };
          miningProgress.current = 0;
          miningState.current = { progress: 0, target: [bx, by, bz] };
        }
      } else {
        miningTarget.current = null;
        miningProgress.current = 0;
        miningState.current = { progress: 0, target: null };
      }
    }

    // --- Check if player is in water ---
    const feetBlockY = pos.y - PLAYER_HEIGHT;
    const headBlockY = pos.y;
    const inWaterFeet = isWater(pos.x, feetBlockY, pos.z);
    const inWaterHead = isWater(pos.x, headBlockY, pos.z);
    isInWater.current = inWaterFeet || inWaterHead;

    // --- Movement direction ---
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    forward.y = 0;
    forward.normalize();

    const right = new THREE.Vector3();
    right.crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();

    const speed = isInWater.current ? SWIM_SPEED : MOVE_SPEED;

    const moveDir = new THREE.Vector3();
    if (keys.current['KeyW'] || keys.current['ArrowUp']) moveDir.add(forward);
    if (keys.current['KeyS'] || keys.current['ArrowDown']) moveDir.sub(forward);
    if (keys.current['KeyA'] || keys.current['ArrowLeft']) moveDir.sub(right);
    if (keys.current['KeyD'] || keys.current['ArrowRight']) moveDir.add(right);
    if (moveDir.length() > 0) moveDir.normalize();

    // --- Gravity / buoyancy ---
    if (isInWater.current) {
      // Water drag: slow down vertical velocity
      velocity.current.y *= 0.9;
      velocity.current.y += WATER_GRAVITY * dt;
      // Clamp downward speed in water
      if (velocity.current.y < -3) velocity.current.y = -3;

      // Space to swim up
      if (keys.current['Space']) {
        velocity.current.y = SWIM_UP_FORCE;
      }
    } else {
      // Normal gravity
      velocity.current.y += GRAVITY * dt;

      // Jump
      if (keys.current['Space'] && isGrounded.current) {
        velocity.current.y = JUMP_FORCE;
        isGrounded.current = false;
      }
    }

    // --- Resolve Y axis first, so X/Z checks use correct feet position ---
    let newY = pos.y + velocity.current.y * dt;

    // Ground collision (falling)
    if (velocity.current.y < 0) {
      const checkFeetY = newY - PLAYER_HEIGHT;
      if (isBlockSolid(pos.x, checkFeetY, pos.z)) {
        newY = Math.floor(checkFeetY) + 1 + PLAYER_HEIGHT;
        velocity.current.y = 0;
        isGrounded.current = true;
      } else {
        if (!isInWater.current) {
          isGrounded.current = false;
        }
      }
    }

    // Ceiling collision (rising)
    if (velocity.current.y > 0) {
      if (isBlockSolid(pos.x, newY + 0.1, pos.z)) {
        velocity.current.y = 0;
      }
    }

    // --- Now resolve X/Z using the corrected Y ---
    // Use a small epsilon above the ground so feet checks don't clip into the floor
    const resolvedFeetY = newY - PLAYER_HEIGHT + 0.01;

    // X-axis collision
    let newX = pos.x + moveDir.x * speed * dt;
    if (isBlockSolid(newX + PLAYER_RADIUS, resolvedFeetY, pos.z) ||
        isBlockSolid(newX + PLAYER_RADIUS, resolvedFeetY + 1, pos.z) ||
        isBlockSolid(newX - PLAYER_RADIUS, resolvedFeetY, pos.z) ||
        isBlockSolid(newX - PLAYER_RADIUS, resolvedFeetY + 1, pos.z)) {
      newX = pos.x;
    }

    // Z-axis collision
    let newZ = pos.z + moveDir.z * speed * dt;
    if (isBlockSolid(newX, resolvedFeetY, newZ + PLAYER_RADIUS) ||
        isBlockSolid(newX, resolvedFeetY + 1, newZ + PLAYER_RADIUS) ||
        isBlockSolid(newX, resolvedFeetY, newZ - PLAYER_RADIUS) ||
        isBlockSolid(newX, resolvedFeetY + 1, newZ - PLAYER_RADIUS)) {
      newZ = pos.z;
    }

    camera.position.set(newX, newY, newZ);

    // Load chunks as player moves
    const cx = Math.floor(newX / 16);
    const cz = Math.floor(newZ / 16);
    if (cx !== lastChunkCheck.current.x || cz !== lastChunkCheck.current.z) {
      lastChunkCheck.current = { x: cx, z: cz };
      onLoadChunks(newX, newZ);
    }
  });

  return null;
}
