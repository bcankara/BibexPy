"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft, ChevronRight, ChevronUp, ChevronDown, ChevronsUpDown,
  Columns3, Eye, EyeOff, Pencil, Trash2,
} from "lucide-react";
import {
  ColumnDef, ColumnSizingState, SortingState, VisibilityState,
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { uidKey, type FilterResponse, type RecordRow } from "@/lib/api-client";
import { Button } from "./Button";
import { useI18n, useT } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { cn } from "@/lib/cn";

type Row = RecordRow;

type Props = {
  data: FilterResponse;
  onPage: (offset: number) => void;
  onRowClick?: (row: Row) => void;
  storageKey?: string;
  selected?: Set<string>;
  onSelectionChange?: (next: Set<string>) => void;
  onEditRow?: (row: Row) => void;
  onDeleteRow?: (row: Row) => void;
};

/** Get translated column label, falling back to the column code itself. */
function getColumnLabel(t: (k: string) => string, col: string): string {
  // Try recordDetail.fields.<COL> first
  const key = `recordDetail.fields.${col}`;
  const v = t(key);
  if (v !== key) return v;
  // Special cases not in recordDetail
  const specials: Record<string, string> = {
    AB_LEN: t("records.columns.abLen"),
    AF_FULL: t("records.columns.afFull"),
  };
  return specials[col] ?? col;
}

const COLUMN_WIDTHS: Record<string, number> = {
  TI: 360, AU: 220, SO: 240, JI: 180, PY: 70, TC: 70, DT: 110, LA: 90,
  DI: 200, AB: 480, DE: 220, ID: 220, C1: 320, WC: 220, SC: 220, DB: 80,
};

// Default visible columns (the rest are hidden but can be enabled from the menu)
const DEFAULT_VISIBLE = new Set(["TI", "AU", "SO", "PY", "TC", "DT", "LA"]);
// UID column — always present for selection but hidden in the table
const ALWAYS_HIDDEN = new Set(["UID"]);

export function RecordsTable({
  data, onPage, onRowClick, storageKey = "bibex.records.table",
  selected, onSelectionChange, onEditRow, onDeleteRow,
}: Props) {
  const { t, locale } = useI18n();
  const { total, offset, limit, columns: cols, records } = data;
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);

  // Selection identity is the record UID and nothing else (sequential integer keys, always
  // compared as text). A row without a UID has no identity (key = "") and cannot be selected.
  const getKey = (r: Row): string => uidKey(r.UID);
  const isSelectable = (r: Row) => getKey(r) !== "";
  const selectionEnabled = !!onSelectionChange;
  const actionsEnabled = !!(onEditRow || onDeleteRow);
  const stickyCol = selectionEnabled || actionsEnabled;
  const selectedSet = selected ?? new Set<string>();

  // Are all rows on the current page selected?
  const pageKeys = records.map(getKey).filter((k) => k !== "");
  const allChecked = pageKeys.length > 0 && pageKeys.every((k) => selectedSet.has(k));
  const someChecked = !allChecked && pageKeys.some((k) => selectedSet.has(k));

  function togglePage() {
    if (!onSelectionChange) return;
    const next = new Set(selectedSet);
    if (allChecked) {
      pageKeys.forEach((k) => next.delete(k));
    } else {
      pageKeys.forEach((k) => next.add(k));
    }
    onSelectionChange(next);
  }
  function toggleRow(r: Row) {
    if (!onSelectionChange) return;
    const k = getKey(r);
    if (!k) return;
    const next = new Set(selectedSet);
    if (next.has(k)) next.delete(k); else next.add(k);
    onSelectionChange(next);
  }

  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem(`${storageKey}.visibility`);
        if (saved) {
          const parsed = JSON.parse(saved) as VisibilityState;
          // Always keep internal fields such as UID hidden
          for (const k of ALWAYS_HIDDEN) parsed[k] = false;
          return parsed;
        }
      } catch {}
    }
    const v: VisibilityState = {};
    for (const c of cols) v[c] = !ALWAYS_HIDDEN.has(c) && DEFAULT_VISIBLE.has(c);
    return v;
  });
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem(`${storageKey}.sizing`);
        if (saved) return JSON.parse(saved);
      } catch {}
    }
    return {};
  });

  useEffect(() => {
    // On first render, set newly arrived columns according to the defaults
    setColumnVisibility((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const c of cols) {
        if (!(c in next)) { next[c] = DEFAULT_VISIBLE.has(c); changed = true; }
      }
      return changed ? next : prev;
    });
  }, [cols]);

  useEffect(() => {
    try { localStorage.setItem(`${storageKey}.visibility`, JSON.stringify(columnVisibility)); } catch {}
  }, [columnVisibility, storageKey]);
  useEffect(() => {
    try { localStorage.setItem(`${storageKey}.sizing`, JSON.stringify(columnSizing)); } catch {}
  }, [columnSizing, storageKey]);

  const columnDefs = useMemo<ColumnDef<Row>[]>(() => cols.map((c) => ({
    id: c,
    accessorKey: c,
    header: getColumnLabel(t, c),
    size: COLUMN_WIDTHS[c] ?? 140,
    minSize: 60,
    maxSize: 800,
    cell: (info) => {
      const raw = info.getValue() as string | number | null;
      const v = raw == null ? "" : String(raw);
      if (v === "") return <span className="text-slate-300">—</span>;
      // Numeric columns
      if (c === "PY" || c === "TC" || c === "NR") {
        return <span className="font-mono tabular-nums">{v}</span>;
      }
      // DOI clickable
      if (c === "DI") {
        return (
          <a
            href={`https://doi.org/${v}`}
            target="_blank" rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-brand-600 hover:underline truncate block"
          >
            {v}
          </a>
        );
      }
      return <span className="truncate block" title={v}>{v}</span>;
    },
  })), [cols, t]);

  const table = useReactTable({
    data: records,
    columns: columnDefs,
    state: { sorting, columnVisibility, columnSizing },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnSizingChange: setColumnSizing,
    // Row id = UID (never the page-local array index). Rows without a UID fall
    // back to a page-local marker that is only used as a React key.
    getRowId: (r, i) => (uidKey(r.UID) !== "" ? uidKey(r.UID) : `__nouid_${i}`),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: "onChange",
    enableColumnResizing: true,
  });

  const scrollerRef = useRef<HTMLDivElement>(null);
  const rows = table.getRowModel().rows;
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => 36,
    overscan: 8,
  });

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.floor(offset / limit) + 1;
  const [columnMenu, setColumnMenu] = useState(false);

  const visibleCount = Object.values(columnVisibility).filter((v) => v).length || cols.length;

  return (
    <div className="space-y-3">
      {/* Top bar: count + pagination + column settings */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-sm text-muted">
          <span className="text-ink font-semibold">{formatNumber(start, locale)}–{formatNumber(end, locale)}</span>{" "}
          / {formatNumber(total, locale)} {t("common.records")}
        </span>

        <div className="ml-auto flex items-center gap-1.5">
          <ColumnPicker
            table={table}
            cols={cols}
            open={columnMenu}
            setOpen={setColumnMenu}
            visibleCount={visibleCount}
            totalCount={cols.length}
          />

          <div className="h-5 w-px bg-border mx-1" />

          <Button size="sm" variant="secondary" disabled={currentPage === 1} onClick={() => onPage(0)} title={t("records.firstPage")}>
            <ChevronsLeft className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="secondary" disabled={currentPage === 1} onClick={() => onPage(Math.max(0, offset - limit))}>
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="text-xs text-muted px-2 tabular-nums">
            {currentPage} / {totalPages}
          </span>
          <Button size="sm" variant="secondary" disabled={end >= total} onClick={() => onPage(offset + limit)}>
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="secondary" disabled={end >= total} onClick={() => onPage((totalPages - 1) * limit)} title={t("records.lastPage")}>
            <ChevronsRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border bg-bg-card shadow-card overflow-hidden">
        <div
          ref={scrollerRef}
          className="overflow-auto"
          style={{ height: Math.min(640, Math.max(280, rows.length * 36 + 44)) }}
        >
          <table
            className="text-xs border-separate border-spacing-0"
            style={{ width: table.getTotalSize(), tableLayout: "fixed" }}
          >
            <thead className="sticky top-0 z-20">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {stickyCol && (
                    <th
                      style={{ width: actionsEnabled ? 96 : 36 }}
                      className="sticky left-0 z-30 bg-bg-soft border-b border-border px-2 py-2.5 text-center"
                    >
                      {selectionEnabled && (
                        <input
                          type="checkbox"
                          checked={allChecked}
                          disabled={pageKeys.length === 0}
                          ref={(el) => { if (el) el.indeterminate = someChecked; }}
                          onChange={togglePage}
                          className="accent-brand-500 h-3.5 w-3.5"
                          title={allChecked ? t("records.unselectPage") : t("records.selectAllInPage")}
                        />
                      )}
                    </th>
                  )}
                  {hg.headers.map((h) => {
                    const canSort = h.column.getCanSort();
                    const sorted = h.column.getIsSorted();
                    return (
                      <th
                        key={h.id}
                        style={{ width: h.getSize() }}
                        className="relative bg-bg-soft border-b border-border text-left px-3 py-2.5 font-semibold text-[11px] uppercase tracking-wide text-muted select-none group"
                      >
                        <div
                          onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                          className={cn(
                            "flex items-center gap-1.5 truncate",
                            canSort && "cursor-pointer hover:text-ink",
                          )}
                        >
                          <span className="truncate">
                            {flexRender(h.column.columnDef.header, h.getContext())}
                          </span>
                          {canSort && (
                            <span className="flex-shrink-0 opacity-60">
                              {sorted === "asc" ? <ChevronUp className="h-3 w-3" /> :
                               sorted === "desc" ? <ChevronDown className="h-3 w-3" /> :
                               <ChevronsUpDown className="h-3 w-3" />}
                            </span>
                          )}
                        </div>
                        {/* Resize handle */}
                        {h.column.getCanResize() && (
                          <div
                            onMouseDown={h.getResizeHandler()}
                            onTouchStart={h.getResizeHandler()}
                            className={cn(
                              "absolute right-0 top-0 h-full w-1.5 cursor-col-resize select-none touch-none",
                              "bg-transparent hover:bg-brand-500/40 active:bg-brand-500",
                              h.column.getIsResizing() && "bg-brand-500",
                            )}
                          />
                        )}
                      </th>
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={table.getVisibleFlatColumns().length + (stickyCol ? 1 : 0)} className="text-center py-12 text-muted">
                    {t("records.noResults")}
                  </td>
                </tr>
              ) : (
                <>
                  {/* Top spacer */}
                  {virt.getVirtualItems()[0]?.start > 0 && (
                    <tr>
                      <td colSpan={table.getVisibleFlatColumns().length + (stickyCol ? 1 : 0)} style={{ height: virt.getVirtualItems()[0].start, padding: 0, border: 0 }} />
                    </tr>
                  )}
                  {virt.getVirtualItems().map((vi) => {
                    const row = rows[vi.index];
                    const rowSelectable = isSelectable(row.original);
                    const isSelected = selectionEnabled && rowSelectable && selectedSet.has(getKey(row.original));
                    return (
                      <tr
                        key={row.id}
                        className={cn(
                          "group cursor-pointer transition",
                          isSelected ? "bg-brand-50/80" : vi.index % 2 === 0 ? "bg-bg-card" : "bg-bg-soft/40",
                          isSelected ? "hover:bg-brand-100/80" : "hover:bg-brand-50/60",
                        )}
                        onClick={(e) => {
                          if ((e.target as HTMLElement).closest("[data-no-row-click]")) return;
                          onRowClick?.(row.original);
                        }}
                      >
                        {stickyCol && (
                          <td
                            data-no-row-click
                            style={{ width: actionsEnabled ? 96 : 36 }}
                            className={cn(
                              "sticky left-0 z-10 border-b border-border/60 px-2 py-2",
                              isSelected ? "bg-brand-50/80" : vi.index % 2 === 0 ? "bg-bg-card" : "bg-bg-soft/40",
                            )}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <div className="flex items-center justify-center gap-1">
                              {selectionEnabled && (
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  disabled={!rowSelectable}
                                  title={rowSelectable ? undefined : t("records.noUidCannotSelect")}
                                  onChange={() => toggleRow(row.original)}
                                  className="accent-brand-500 h-3.5 w-3.5"
                                />
                              )}
                              {onEditRow && (
                                <button
                                  onClick={() => onEditRow(row.original)}
                                  className="text-muted hover:text-brand-600 p-0.5 rounded"
                                  title={t("records.editRow")}
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                              )}
                              {onDeleteRow && (
                                <button
                                  onClick={() => onDeleteRow(row.original)}
                                  className="text-muted hover:text-danger p-0.5 rounded"
                                  title={t("records.deleteRow")}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </div>
                          </td>
                        )}
                        {row.getVisibleCells().map((cell) => (
                          <td
                            key={cell.id}
                            style={{ width: cell.column.getSize() }}
                            className="border-b border-border/60 px-3 py-2 align-top text-ink overflow-hidden"
                          >
                            <div className="truncate">
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </div>
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                  {/* Bottom spacer */}
                  {(() => {
                    const items = virt.getVirtualItems();
                    if (items.length === 0) return null;
                    const last = items[items.length - 1];
                    const tail = virt.getTotalSize() - last.end;
                    if (tail <= 0) return null;
                    return (
                      <tr>
                        <td colSpan={table.getVisibleFlatColumns().length + (stickyCol ? 1 : 0)} style={{ height: tail, padding: 0, border: 0 }} />
                      </tr>
                    );
                  })()}
                </>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bottom: page size info + hint */}
      <p className="text-[11px] text-muted">
        {t("records.tableHint")}
      </p>
    </div>
  );
}

function ColumnPicker({ table, cols, open, setOpen, visibleCount, totalCount }: {
  table: ReturnType<typeof useReactTable<Row>>;
  cols: string[];
  open: boolean;
  setOpen: (v: boolean) => void;
  visibleCount: number;
  totalCount: number;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open, setOpen]);

  const filtered = cols.filter((c) => {
    if (ALWAYS_HIDDEN.has(c)) return false; // hide internal columns (UID)
    if (!search) return true;
    const s = search.toLowerCase();
    return c.toLowerCase().includes(s) || getColumnLabel(t, c).toLowerCase().includes(s);
  });

  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="secondary" onClick={() => setOpen(!open)} title={t("records.columns.configure")}>
        <Columns3 className="h-3.5 w-3.5" />
        <span className="hidden md:inline">{t("records.columns.short")}</span>
        <span className="text-[10px] text-muted ml-1">{visibleCount}/{totalCount}</span>
      </Button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-72 rounded-lg border border-border bg-white shadow-soft z-50 overflow-hidden">
          <div className="p-2 border-b border-border">
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("records.columns.searchPlaceholder")}
              className="w-full rounded-md border border-border bg-bg-soft px-2 py-1.5 text-sm focus:outline-none focus:border-brand-500"
            />
          </div>
          <div className="px-2 py-1.5 flex items-center gap-2 border-b border-border text-xs">
            <button
              onClick={() => table.toggleAllColumnsVisible(true)}
              className="text-brand-600 hover:underline"
            >
              {t("records.columns.all")}
            </button>
            <span className="text-border">·</span>
            <button
              onClick={() => {
                // Enable only the defaults
                table.getAllLeafColumns().forEach((c) => {
                  c.toggleVisibility(DEFAULT_VISIBLE.has(c.id));
                });
              }}
              className="text-brand-600 hover:underline"
            >
              {t("records.columns.defaults")}
            </button>
            <span className="text-border">·</span>
            <button
              onClick={() => table.toggleAllColumnsVisible(false)}
              className="text-brand-600 hover:underline"
            >
              {t("records.columns.none")}
            </button>
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="text-xs text-muted px-3 py-3 text-center">{t("records.columns.noMatch")}</p>
            ) : filtered.map((cid) => {
              const col = table.getColumn(cid);
              if (!col) return null;
              const visible = col.getIsVisible();
              return (
                <button
                  key={cid}
                  onClick={() => col.toggleVisibility()}
                  className="w-full text-left px-3 py-1.5 text-sm flex items-center gap-2 hover:bg-bg-soft"
                >
                  {visible ? <Eye className="h-3.5 w-3.5 text-brand-500" /> : <EyeOff className="h-3.5 w-3.5 text-muted" />}
                  <span className={cn("font-mono text-xs w-12 flex-shrink-0", visible ? "text-ink" : "text-muted")}>{cid}</span>
                  <span className={cn("truncate", visible ? "text-ink" : "text-muted")}>{getColumnLabel(t, cid)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// Helper: the ChevronsLeft/Right icons exist in lucide, but import them
function ChevronsLeft({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="11 17 6 12 11 7" />
      <polyline points="18 17 13 12 18 7" />
    </svg>
  );
}
function ChevronsRight({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="13 17 18 12 13 7" />
      <polyline points="6 17 11 12 6 7" />
    </svg>
  );
}
