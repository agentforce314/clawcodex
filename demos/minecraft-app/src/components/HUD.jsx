import { useEffect } from 'react';
import { INVENTORY_BLOCKS, BlockColors, BlockNames } from '../utils/blocks';

export default function HUD({ selectedBlockIndex, onSelectBlock, onCycleBlock }) {
  useEffect(() => {
    const onKeyDown = (e) => {
      const num = parseInt(e.key);
      if (num >= 1 && num <= 9) {
        onSelectBlock(num - 1);
      }
    };

    const onWheel = (e) => {
      onCycleBlock(e.deltaY > 0 ? 1 : -1);
    };

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('wheel', onWheel);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('wheel', onWheel);
    };
  }, [onSelectBlock, onCycleBlock]);

  return (
    <div style={{
      position: 'fixed',
      bottom: '20px',
      left: '50%',
      transform: 'translateX(-50%)',
      display: 'flex',
      gap: '4px',
      padding: '6px',
      background: 'rgba(0, 0, 0, 0.6)',
      borderRadius: '6px',
      zIndex: 100,
      userSelect: 'none',
    }}>
      {INVENTORY_BLOCKS.map((blockType, index) => (
        <div
          key={blockType}
          style={{
            width: '48px',
            height: '48px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            border: index === selectedBlockIndex
              ? '3px solid #fff'
              : '2px solid rgba(255,255,255,0.2)',
            borderRadius: '4px',
            cursor: 'pointer',
            position: 'relative',
            background: index === selectedBlockIndex
              ? 'rgba(255,255,255,0.15)'
              : 'transparent',
          }}
          onClick={() => onSelectBlock(index)}
        >
          <div
            style={{
              width: '28px',
              height: '28px',
              background: BlockColors[blockType],
              borderRadius: '3px',
              border: '1px solid rgba(0,0,0,0.3)',
              boxShadow: 'inset 1px 1px 2px rgba(255,255,255,0.2), inset -1px -1px 2px rgba(0,0,0,0.3)',
            }}
          />
          <span style={{
            position: 'absolute',
            top: '1px',
            left: '4px',
            fontSize: '10px',
            color: 'rgba(255,255,255,0.6)',
            fontFamily: 'monospace',
          }}>
            {index + 1}
          </span>
        </div>
      ))}
      <div style={{
        position: 'absolute',
        bottom: '-22px',
        left: '50%',
        transform: 'translateX(-50%)',
        color: '#fff',
        fontSize: '12px',
        fontFamily: '"Courier New", monospace',
        whiteSpace: 'nowrap',
        textShadow: '1px 1px 2px rgba(0,0,0,0.8)',
      }}>
        {BlockNames[INVENTORY_BLOCKS[selectedBlockIndex]]}
      </div>
    </div>
  );
}
