import type { Tray } from "../types";

type SortKey = keyof Tray;

interface Column {
  key: SortKey;
  label: string;
  align?: "left" | "right" | "center";
  render?: (t: Tray) => React.ReactNode;
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-blue-500/15 text-blue-300",
  cooled: "bg-cyan-500/15 text-cyan-300",
  released: "bg-emerald-500/15 text-emerald-300",
  removed: "bg-slate-500/15 text-slate-400",
};

function StatusBadge({ status }: { status: string | null }) {
  const s = status ?? "—";
  const cls = STATUS_STYLES[s] ?? "bg-slate-500/15 text-slate-400";
  return (
    <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {s}
    </span>
  );
}

const COLUMNS: Column[] = [
  { key: "tray_number", label: "Tray #" },
  { key: "sample_name", label: "Sample" },
  { key: "incubator_name", label: "Incubator" },
  {
    key: "parasite_level_pct",
    label: "Parasite",
    align: "right",
    render: (t) => (t.parasite_level_pct == null ? "—" : `${t.parasite_level_pct}%`),
  },
  { key: "in_date", label: "Start" },
  {
    key: "cool_days",
    label: "Cool",
    align: "right",
    render: (t) => (t.cool_days == null ? "—" : `${t.cool_days}d`),
  },
  {
    key: "status",
    label: "Status",
    align: "center",
    render: (t) => <StatusBadge status={t.status} />,
  },
];

interface Props {
  trays: Tray[];
  selected: Set<number>;
  sortCol: SortKey;
  sortAsc: boolean;
  onSort: (key: SortKey) => void;
  onRowClick: (e: React.MouseEvent, id: number) => void;
}

export default function TraysTable({
  trays,
  selected,
  sortCol,
  sortAsc,
  onSort,
  onRowClick,
}: Props) {
  return (
    <div className="overflow-auto rounded-xl border border-slate-700/60">
      <table className="w-full border-collapse text-sm select-none">
        <thead className="sticky top-0 bg-slate-800/90 backdrop-blur">
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={String(c.key)}
                onClick={() => onSort(c.key)}
                className={`cursor-pointer px-3 py-2 font-semibold text-amber-400 hover:bg-slate-700/50 ${
                  c.align === "right"
                    ? "text-right"
                    : c.align === "center"
                    ? "text-center"
                    : "text-left"
                }`}
              >
                {c.label}
                {sortCol === c.key ? (sortAsc ? " ↑" : " ↓") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trays.map((t, i) => {
            const sel = selected.has(t.id);
            return (
              <tr
                key={t.id}
                onClick={(e) => onRowClick(e, t.id)}
                className={`cursor-pointer ${
                  sel
                    ? "bg-[#26374f]"
                    : i % 2 === 0
                    ? "bg-slate-900/40"
                    : "bg-slate-800/30"
                } hover:bg-slate-700/40`}
              >
                {COLUMNS.map((c) => (
                  <td
                    key={String(c.key)}
                    className={`px-3 py-1.5 ${
                      c.align === "right"
                        ? "text-right"
                        : c.align === "center"
                        ? "text-center"
                        : "text-left"
                    } ${c.key === "incubator_name" ? "text-slate-400" : ""}`}
                  >
                    {c.render ? c.render(t) : ((t[c.key] as React.ReactNode) ?? "—")}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
