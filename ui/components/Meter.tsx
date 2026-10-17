// Horizontal 0–1 meter, coloured by risk band. Used for denial risk + fraud score.
// `label` names what is measured; the tooltip then reads e.g. "Denial risk: 87%".
export default function Meter({ value, warnAt = 0.3, badAt = 0.6, label }: { value: number; warnAt?: number; badAt?: number; label?: string }) {
  const color = value >= badAt ? "var(--bad)" : value >= warnAt ? "var(--warn)" : "var(--good)";
  const level = value >= badAt ? "high" : value >= warnAt ? "medium" : "low";
  return (
    <div className="meter" data-tip={`${label ? label + ": " : ""}${(value * 100).toFixed(1)}% (${level})`}>
      <div className="track">
        <div className="fill" style={{ width: `${Math.max(2, value * 100)}%`, background: color }} />
      </div>
      <span className="val">{Math.round(value * 100)}%</span>
    </div>
  );
}
