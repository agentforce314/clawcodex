export default function Crosshair() {
  return (
    <div
      style={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        pointerEvents: 'none',
        zIndex: 100,
      }}
    >
      <svg width="24" height="24" viewBox="0 0 24 24">
        <line x1="12" y1="4" x2="12" y2="10" stroke="white" strokeWidth="2" opacity="0.8" />
        <line x1="12" y1="14" x2="12" y2="20" stroke="white" strokeWidth="2" opacity="0.8" />
        <line x1="4" y1="12" x2="10" y2="12" stroke="white" strokeWidth="2" opacity="0.8" />
        <line x1="14" y1="12" x2="20" y2="12" stroke="white" strokeWidth="2" opacity="0.8" />
      </svg>
    </div>
  );
}
