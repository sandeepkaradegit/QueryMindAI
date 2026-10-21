type Point = { label: string; value: number };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function toNumber(value: unknown): number | null {
  if (isFiniteNumber(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function buildPoints(columns: string[], rows: Record<string, unknown>[]): Point[] {
  if (columns.length < 1 || columns.length > 2 || rows.length === 0) {
    return [];
  }
  const [first, second] = columns;
  if (!second) {
    const points = rows
      .map((row, index) => ({ label: `${index + 1}`, value: toNumber(row[first]) }))
      .filter((point): point is { label: string; value: number } => point.value !== null);
    return points;
  }
  const numericFirst = rows.filter((row) => toNumber(row[first]) !== null).length;
  const numericSecond = rows.filter((row) => toNumber(row[second]) !== null).length;
  const numericColumn = numericSecond >= numericFirst ? second : first;
  const labelColumn = numericColumn === second ? first : second;
  return rows
    .map((row, index) => {
      const value = toNumber(row[numericColumn]);
      if (value === null) {
        return null;
      }
      const labelValue = row[labelColumn];
      const label = labelValue === null || labelValue === undefined || labelValue === "" ? `${index + 1}` : String(labelValue);
      return { label, value };
    })
    .filter((point): point is Point => point !== null);
}

export function SimpleChart({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  const points = buildPoints(columns, rows);
  if (points.length === 0) {
    return null;
  }
  const maxValue = Math.max(...points.map((point) => point.value));
  if (!Number.isFinite(maxValue) || maxValue <= 0) {
    return null;
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="muted" style={{ marginBottom: 8 }}>
        Auto chart preview
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        {points.slice(0, 20).map((point) => (
          <div key={`${point.label}:${point.value}`} style={{ display: "grid", gridTemplateColumns: "120px 1fr 70px", gap: 8, alignItems: "center" }}>
            <span className="muted" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {point.label}
            </span>
            <div style={{ height: 14, background: "#eef2ff", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ width: `${Math.max((point.value / maxValue) * 100, 2)}%`, height: "100%", background: "#6366f1" }} />
            </div>
            <span className="muted" style={{ textAlign: "right" }}>{point.value.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
