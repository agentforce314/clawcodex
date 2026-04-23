import { useRef, useEffect } from 'react';

export default function MiningIndicator({ miningState }) {
  const barRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    let animId;
    const update = () => {
      const state = miningState.current;
      if (state && state.progress > 0 && state.target) {
        containerRef.current.style.display = 'block';
        barRef.current.style.width = `${state.progress * 100}%`;
      } else {
        containerRef.current.style.display = 'none';
      }
      animId = requestAnimationFrame(update);
    };
    animId = requestAnimationFrame(update);
    return () => cancelAnimationFrame(animId);
  }, [miningState]);

  return (
    <div
      ref={containerRef}
      style={{
        display: 'none',
        position: 'fixed',
        top: '55%',
        left: '50%',
        transform: 'translateX(-50%)',
        width: '120px',
        height: '6px',
        background: 'rgba(0, 0, 0, 0.5)',
        borderRadius: '3px',
        overflow: 'hidden',
        zIndex: 100,
        border: '1px solid rgba(255, 255, 255, 0.2)',
      }}
    >
      <div
        ref={barRef}
        style={{
          height: '100%',
          width: '0%',
          background: 'linear-gradient(90deg, #e0e0e0, #ffffff)',
          borderRadius: '3px',
          transition: 'width 0.05s linear',
        }}
      />
    </div>
  );
}
