// Horizontal 0–1 meter, coloured by risk band. Used for denial risk + fraud score.
export default function Meter({ value, warnAt = 0.3, badAt = 0.6 }: { value: number; warnAt?: number; badAt?: number }) {
  const color = value >= badAt ? "var(--bad)" : value >= warnAt ? "var(--warn)" : "var(--good)";
  return (
    <div className="meter" title={value.toFixed(3)}>
      <div className="track">
        <div className="fill" style={{ width: `${Math.max(2, value * 100)}%`, background: color }} />
      </div>
      <span className="val">{Math.round(value * 100)}%</span>
    </div>
  );
}
