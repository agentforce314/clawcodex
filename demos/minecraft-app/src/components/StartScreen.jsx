export default function StartScreen({ onStart }) {
  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
      zIndex: 1000,
      fontFamily: '"Courier New", monospace',
      color: '#fff',
    }}>
      <h1 style={{
        fontSize: '64px',
        fontWeight: 'bold',
        letterSpacing: '4px',
        textShadow: '3px 3px 0px #333, 6px 6px 0px rgba(0,0,0,0.3)',
        marginBottom: '8px',
        color: '#5cb85c',
      }}>
        MINECRAFT
      </h1>
      <p style={{
        fontSize: '16px',
        color: 'rgba(255,255,255,0.5)',
        marginBottom: '60px',
        letterSpacing: '6px',
      }}>
        REACT EDITION
      </p>

      <button
        onClick={onStart}
        style={{
          padding: '16px 48px',
          fontSize: '20px',
          fontFamily: '"Courier New", monospace',
          background: 'rgba(92, 184, 92, 0.8)',
          color: '#fff',
          border: '3px solid rgba(255,255,255,0.3)',
          borderRadius: '4px',
          cursor: 'pointer',
          letterSpacing: '2px',
          transition: 'all 0.2s',
          marginBottom: '20px',
        }}
        onMouseEnter={(e) => {
          e.target.style.background = 'rgba(92, 184, 92, 1)';
          e.target.style.transform = 'scale(1.05)';
        }}
        onMouseLeave={(e) => {
          e.target.style.background = 'rgba(92, 184, 92, 0.8)';
          e.target.style.transform = 'scale(1)';
        }}
      >
        Singleplayer
      </button>

      <div style={{
        marginTop: '60px',
        fontSize: '13px',
        color: 'rgba(255,255,255,0.4)',
        textAlign: 'center',
        lineHeight: '2',
      }}>
        <p>WASD - Move | Space - Jump | Mouse - Look</p>
        <p>Left Click - Break Block | Right Click - Place Block</p>
        <p>Scroll / 1-9 - Select Block</p>
      </div>
    </div>
  );
}
