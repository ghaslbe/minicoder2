import type { ReactNode } from "react";

export type Column<T> = { key: string; label: string; render: (row: T) => ReactNode };
export function DataTable<T extends { id: string }>({
  rows,
  columns,
  loading,
}: {
  rows: T[];
  columns: Column<T>[];
  loading?: boolean;
}) {
  if (loading)
    return (
      <div className="empty" role="status">
        Kunden werden geladen…
      </div>
    );
  if (!rows.length)
    return (
      <div className="empty" role="status">
        Keine Eintraege gefunden.
      </div>
    );
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Kundenliste">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th scope="col" key={c.key}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((c) => (
                <td key={c.key}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
