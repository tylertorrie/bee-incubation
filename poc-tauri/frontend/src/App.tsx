import { useEffect, useMemo, useRef, useState } from "react";
import type { Incubator, Tray } from "./types";
import { getIncubators, getTrays } from "./api";
import TraysTable from "./components/TraysTable";

type SortKey = keyof Tray;

export default function App() {
  const [incubators, setIncubators] = useState<Incubator[]>([]);
  const [filterInc, setFilterInc] = useState<number | undefined>(undefined);
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [trays, setTrays] = useState<Tray[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchorRef = useRef<number | null>(null);

  const [sortCol, setSortCol] = useState<SortKey>("tray_number");
  const [sortAsc, setSortAsc] = useState(true);

  const [moveDest, setMoveDest] = useState<number | undefined>(undefined);

  // Load incubators once
  useEffect(() => {
    getIncubators()
      .then(setIncubators)
      .catch((e) => setError(String(e)));
  }, []);

  // Load trays whenever filters change
  useEffect(() => {
    setLoading(true);
    setError(null);
    getTrays({ incubator_id: filterInc, status: filterStatus || undefined })
      .then((rows) => {
        setTrays(rows);
        setSelected(new Set());
        anchorRef.current = null;
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [filterInc, filterStatus]);

  // Client-side sort (same order the selection ranges use)
  const sorted = useMemo(() => {
    const rows = [...trays];
    rows.sort((a, b) => {
      const av = a[sortCol];
      const bv = b[sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return String(av).localeCompare(String(bv), undefined, { numeric: true });
    });
    if (!sortAsc) rows.reverse();
    return rows;
  }, [trays, sortCol, sortAsc]);

  function handleSort(key: SortKey) {
    if (key === sortCol) setSortAsc((v) => !v);
    else {
      setSortCol(key);
      setSortAsc(true);
    }
  }

  // Click = toggle + set anchor; Shift+click = select range (across the
  // current sorted order) — mirrors the desktop app's selection behaviour.
  function handleRowClick(e: React.MouseEvent, id: number) {
    const ids = sorted.map((t) => t.id);
    setSelected((prev) => {
      const next = new Set(prev);
      if (e.shiftKey && anchorRef.current != null) {
        const i0 = ids.indexOf(anchorRef.current);
        const i1 = ids.indexOf(id);
        if (i0 !== -1 && i1 !== -1) {
          const [lo, hi] = i0 <= i1 ? [i0, i1] : [i1, i0];
          for (let i = lo; i <= hi; i++) next.add(ids[i]);
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      anchorRef.current = id;
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set(sorted.map((t) => t.id)));
  }
  function clearSel() {
    setSelected(new Set());
    anchorRef.current = null;
  }

  function doMove() {
    const dest = incubators.find((i) => i.id === moveDest);
    // POC is read-only — describe what the real migration would do.
    alert(
      `POC (read-only): would move ${selected.size} tray(s) to ` +
        `${dest?.name ?? "—"} (${dest?.temp_mode ?? "—"}), applying the ` +
        `same cool-day carry-over rules as the desktop app.`,
    );
  }

  const statuses = ["", "active", "cooled", "released", "removed"];

  return (
    <div className="mx-auto max-w-6xl p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <span className="text-2xl">🐝</span>
        <h1 className="text-xl font-bold text-amber-400">Bee Incubation</h1>
        <span className="rounded bg-slate-700/60 px-2 py-0.5 text-xs text-slate-300">
          Tauri · React · TypeScript · Tailwind — POC
        </span>
      </header>

      {/* Filter + action bar */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-700/60 bg-slate-800/40 p-3">
        <label className="text-xs text-slate-400">Incubator</label>
        <select
          className="rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-sm"
          value={filterInc ?? ""}
          onChange={(e) =>
            setFilterInc(e.target.value ? Number(e.target.value) : undefined)
          }
        >
          <option value="">All</option>
          {incubators.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name} ({i.temp_mode})
            </option>
          ))}
        </select>

        <label className="ml-2 text-xs text-slate-400">Status</label>
        <select
          className="rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-sm"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s === "" ? "All" : s}
            </option>
          ))}
        </select>

        <div className="mx-2 h-6 w-px bg-slate-600" />

        <button
          onClick={selectAll}
          className="rounded-md bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
        >
          ☑ Select All
        </button>
        <button
          onClick={clearSel}
          className="rounded-md bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
        >
          Clear
        </button>

        <div className="mx-2 h-6 w-px bg-slate-600" />

        <select
          className="rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-sm"
          value={moveDest ?? ""}
          onChange={(e) =>
            setMoveDest(e.target.value ? Number(e.target.value) : undefined)
          }
        >
          <option value="">Move to…</option>
          {incubators.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name} ({i.temp_mode})
            </option>
          ))}
        </select>
        <button
          onClick={doMove}
          disabled={selected.size === 0 || moveDest == null}
          className="rounded-md bg-amber-500 px-3 py-1 text-sm font-semibold text-slate-900 enabled:hover:bg-amber-400 disabled:opacity-40"
        >
          Move →
        </button>

        <span className="ml-auto text-sm text-teal-300">
          {selected.size
            ? `${selected.size} selected (of ${sorted.length} shown)`
            : `${sorted.length} trays`}
        </span>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          {error}. Is the backend running on :8756?
        </div>
      )}
      {loading ? (
        <div className="p-10 text-center text-slate-500">Loading…</div>
      ) : (
        <TraysTable
          trays={sorted}
          selected={selected}
          sortCol={sortCol}
          sortAsc={sortAsc}
          onSort={handleSort}
          onRowClick={handleRowClick}
        />
      )}

      <p className="mt-4 text-xs text-slate-500">
        This React UI is talking to a FastAPI wrapper around the existing Python
        modules (<code>incubation_db</code>, <code>incubation_calc</code>) — the
        same battle-tested logic, no rewrite. Read-only for the POC.
      </p>
    </div>
  );
}
