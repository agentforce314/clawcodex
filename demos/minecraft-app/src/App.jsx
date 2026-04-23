import { useState, useRef, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import World from './components/World';
import Player from './components/Player';
import Sky from './components/Sky';
import HUD from './components/HUD';
import Crosshair from './components/Crosshair';
import MiningIndicator from './components/MiningIndicator';
import StartScreen from './components/StartScreen';
import { useWorldState } from './hooks/useWorldState';

function Game() {
  const {
    blocks,
    spawnPos,
    selectedBlockIndex,
    addBlock,
    removeBlock,
    selectBlock,
    cycleBlock,
    loadChunksAround,
  } = useWorldState();

  const miningState = useRef({ progress: 0, target: null });

  if (!spawnPos) {
    return (
      <div style={{
        position: 'fixed',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#1a1a2e',
        color: '#fff',
        fontFamily: '"Courier New", monospace',
        fontSize: '20px',
      }}>
        Generating world...
      </div>
    );
  }

  return (
    <>
      <Canvas
        camera={{ fov: 75, near: 0.1, far: 200 }}
        style={{ position: 'fixed', inset: 0 }}
        gl={{ antialias: false, powerPreference: 'high-performance' }}
      >
        <Suspense fallback={null}>
          <Sky />
          <World blocks={blocks} />
          <Player
            spawnPos={spawnPos}
            blocks={blocks}
            onAddBlock={addBlock}
            onRemoveBlock={removeBlock}
            onLoadChunks={loadChunksAround}
            miningState={miningState}
          />
        </Suspense>
      </Canvas>
      <Crosshair />
      <MiningIndicator miningState={miningState} />
      <HUD
        selectedBlockIndex={selectedBlockIndex}
        onSelectBlock={selectBlock}
        onCycleBlock={cycleBlock}
      />
    </>
  );
}

export default function App() {
  const [started, setStarted] = useState(false);

  if (!started) {
    return <StartScreen onStart={() => setStarted(true)} />;
  }

  return <Game />;
}
