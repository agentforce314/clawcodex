export default function StatBar({ label, value, color }) {
  const barColor = color || (value >= 70 ? '#4caf50' : value >= 40 ? '#ff9800' : '#f44336');
  return (
    <div className="stat-bar">
      <span className="stat-bar__label">{label}</span>
      <div className="stat-bar__track">
        <div
          className="stat-bar__fill"
          style={{ width: `${value}%`, backgroundColor: barColor }}
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
      <span className="stat-bar__value">{value}%</span>
    </div>
  );
}
