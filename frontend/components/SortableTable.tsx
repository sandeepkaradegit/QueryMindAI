"use client";

import { useMemo, useState } from "react";

export function SortableTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [ascending, setAscending] = useState(true);

  const sortedRows = useMemo(() => {
    if (!sortColumn) return rows;
    return [...rows].sort((left, right) => {
      const a = left[sortColumn];
      const b = right[sortColumn];
      if (a === b) return 0;
      if (a === null || a === undefined) return 1;
      if (b === null || b === undefined) return -1;
      const comparison =
        typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
      return ascending ? comparison : -comparison;
    });
  }, [rows, sortColumn, ascending]);

  function toggleSort(column: string) {
    if (sortColumn === column) {
      setAscending(!ascending);
    } else {
      setSortColumn(column);
      setAscending(true);
    }
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className="sortable" onClick={() => toggleSort(column)}>
                {column}
                {sortColumn === column ? (ascending ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{row[column] === null ? "—" : String(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
