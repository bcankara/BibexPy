"use client";
import { useCallback, useEffect, useState } from "react";
import {
  Folder, FolderOpen, ChevronRight, Home, Download, FileText, Monitor,
  HardDrive, X, ArrowUp, Loader2, AlertCircle, Check, FolderInput,
} from "lucide-react";
import { api, type BrowseResponse, type BrowseEntry, translateApiError} from "@/lib/api-client";
import { Button } from "./Button";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

declare global {
  interface Window {
    showDirectoryPicker?: (options?: { mode?: "read" | "readwrite"; startIn?: string }) => Promise<FileSystemDirectoryHandle>;
  }
}

type Props = {
  open: boolean;
  initialPath?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
};

const ICON_MAP: Record<string, React.ReactNode> = {
  home: <Home className="h-3.5 w-3.5" />,
  downloads: <Download className="h-3.5 w-3.5" />,
  documents: <FileText className="h-3.5 w-3.5" />,
  desktop: <Monitor className="h-3.5 w-3.5" />,
  computer: <HardDrive className="h-3.5 w-3.5" />,
};

export function FolderPickerModal({ open, initialPath, onClose, onSelect }: Props) {
  const t = useT();
  const [data, setData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const [nativeMatches, setNativeMatches] = useState<{ name: string; matches: { path: string; depth: number; parent: string }[] } | null>(null);
  const [nativeChecking, setNativeChecking] = useState(false);
  const supportsNative = typeof window !== "undefined" && !!window.showDirectoryPicker;

  const browse = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.browseFolder(path);
      setData(r);
    } catch (e) {
      setError(translateApiError(t, e, "folderPicker.nativePickerLoadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!open) {
      setMounted(false);
      return;
    }
    requestAnimationFrame(() => setMounted(true));
    browse(initialPath || "");
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, initialPath, browse, onClose]);

  async function openNativePicker() {
    if (!window.showDirectoryPicker) return;
    setError(null);
    setNativeMatches(null);
    try {
      const handle = await window.showDirectoryPicker({ mode: "read" });
      // Tarayıcı tam path vermiyor — sadece klasör adı
      // Backend ile bu adın olası path'lerini bul
      setNativeChecking(true);
      try {
        const r = await api.findFolderByName(handle.name);
        if (r.matches.length === 1) {
          // Tek eşleşme → direkt seç
          onSelect(r.matches[0].path);
          onClose();
        } else if (r.matches.length === 0) {
          setError(t("folderPicker.nativePickerNoPath", { name: handle.name }));
        } else {
          setNativeMatches(r);
        }
      } finally {
        setNativeChecking(false);
      }
    } catch (e) {
      if ((e as DOMException).name !== "AbortError") {
        setError(translateApiError(t, e, "folderPicker.nativePickerError"));
      }
    }
  }

  if (!open) return null;

  const breadcrumbs = makeBreadcrumbs(data?.current ?? "", data?.platform ?? "unix");

  return (
    <>
      <div
        onClick={onClose}
        className={cn(
          "fixed inset-0 bg-ink/40 backdrop-blur-sm z-[60] transition-opacity",
          mounted ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        className={cn(
          "fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none",
          mounted ? "opacity-100" : "opacity-0",
        )}
      >
        <div
          className={cn(
            "w-full max-w-3xl bg-white rounded-2xl shadow-2xl flex flex-col overflow-hidden pointer-events-auto",
            "transition-transform duration-200",
            mounted ? "scale-100" : "scale-95",
          )}
          style={{ height: "min(620px, 85vh)" }}
        >
          {/* Header */}
          <div className="px-5 py-3 border-b border-border flex items-center gap-3">
            <FolderOpen className="h-5 w-5 text-brand-500" />
            <div className="flex-1 min-w-0">
              <h2 className="font-semibold text-sm">{t("folderPicker.title")}</h2>
              <p className="text-[11px] text-muted">
                {t("folderPicker.subtitle")}
              </p>
            </div>
            {supportsNative && (
              <button
                onClick={openNativePicker}
                disabled={nativeChecking}
                className="text-xs px-2.5 py-1.5 rounded-md border border-border bg-white hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1.5 disabled:opacity-50"
                title={t("folderPicker.nativePickerTip")}
              >
                {nativeChecking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderInput className="h-3.5 w-3.5" />}
                {t("folderPicker.nativePicker")}
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-md hover:bg-bg-soft text-muted hover:text-ink"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Native picker — birden fazla eşleşme bulundu */}
          {nativeMatches && nativeMatches.matches.length > 1 && (
            <div className="px-5 py-3 border-b border-border bg-info-soft/50">
              <p className="text-xs font-medium text-info mb-2">
                {t("folderPicker.nativePickerMultipleMatches", { name: nativeMatches.name, count: nativeMatches.matches.length })}
              </p>
              <div className="space-y-1">
                {nativeMatches.matches.map((m) => (
                  <button
                    key={m.path}
                    onClick={() => { onSelect(m.path); onClose(); }}
                    className="w-full text-left text-xs px-3 py-1.5 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 font-mono truncate"
                  >
                    {m.path}
                  </button>
                ))}
                <button
                  onClick={() => setNativeMatches(null)}
                  className="text-[11px] text-muted hover:text-ink underline"
                >
                  {t("folderPicker.cancelManual")}
                </button>
              </div>
            </div>
          )}

          {/* Breadcrumb */}
          <div className="px-5 py-2 border-b border-border flex items-center gap-1 text-xs bg-bg-soft/40 overflow-x-auto">
            <button
              onClick={() => browse(data?.parent ?? "")}
              disabled={data?.is_root}
              className="p-1 rounded hover:bg-bg-soft disabled:opacity-30 disabled:cursor-not-allowed text-muted hover:text-ink"
              title={t("folderPicker.upBtn")}
            >
              <ArrowUp className="h-3.5 w-3.5" />
            </button>
            <div className="w-px h-4 bg-border mx-1" />
            <button
              onClick={() => browse("")}
              className="px-1.5 py-0.5 hover:bg-bg-soft rounded text-muted hover:text-ink flex items-center gap-1"
              title={t("folderPicker.computerTip")}
            >
              <HardDrive className="h-3 w-3" /> {t("folderPicker.computer")}
            </button>
            {breadcrumbs.map((b, i) => (
              <div key={i} className="flex items-center gap-0.5">
                <ChevronRight className="h-3 w-3 text-muted/50 flex-shrink-0" />
                <button
                  onClick={() => browse(b.path)}
                  className="px-1.5 py-0.5 hover:bg-bg-soft rounded text-ink truncate max-w-[140px]"
                  title={b.path}
                >
                  {b.label}
                </button>
              </div>
            ))}
          </div>

          {/* Body: shortcuts + entries */}
          <div className="flex-1 flex overflow-hidden">
            {/* Shortcuts */}
            <aside className="w-44 border-r border-border bg-bg-soft/30 overflow-y-auto p-2 space-y-0.5 flex-shrink-0">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted px-2 py-1">
                {t("folderPicker.quickAccess")}
              </p>
              {(data?.shortcuts ?? []).map((s) => (
                <button
                  key={s.label}
                  onClick={() => browse(s.path)}
                  className="w-full text-left flex items-center gap-2 px-2 py-1.5 text-xs rounded hover:bg-white text-ink"
                >
                  <span className="text-muted">{ICON_MAP[s.icon] ?? <Folder className="h-3.5 w-3.5" />}</span>
                  <span className="truncate">{s.label}</span>
                </button>
              ))}
            </aside>

            {/* Folder list */}
            <div className="flex-1 overflow-y-auto p-2">
              {loading ? (
                <div className="flex items-center justify-center py-16 text-muted">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> {t("folderPicker.loading")}
                </div>
              ) : error ? (
                <div className="rounded-lg border border-danger/30 bg-danger-soft/40 px-3 py-3 text-sm text-red-700 flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="font-medium">{error}</div>
                    <button
                      onClick={() => browse("")}
                      className="text-xs underline mt-1 hover:opacity-80"
                    >
                      {t("folderPicker.backToRoot")}
                    </button>
                  </div>
                </div>
              ) : (data?.entries ?? []).length === 0 ? (
                <div className="text-center py-12 text-muted text-sm">
                  {t("folderPicker.emptyFolder")}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-1">
                  {(data?.entries ?? []).map((e) => (
                    <FolderItem key={e.path} entry={e} onOpen={() => browse(e.path)} />
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="px-5 py-3 border-t border-border flex items-center gap-3 bg-bg-soft/30">
            <div className="flex-1 min-w-0">
              <div className="text-[10px] text-muted uppercase tracking-wide">{t("folderPicker.toSelect")}</div>
              <div className="text-xs font-mono text-ink truncate" title={data?.current ?? ""}>
                {data?.current || t("folderPicker.nothingSelected")}
              </div>
            </div>
            <Button variant="secondary" onClick={onClose} size="sm">
              {t("folderPicker.cancel")}
            </Button>
            <Button
              onClick={() => { if (data?.current) onSelect(data.current); onClose(); }}
              disabled={!data?.current}
              size="sm"
            >
              <Check className="h-3.5 w-3.5" />
              {t("folderPicker.selectThis")}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

function FolderItem({ entry, onOpen }: { entry: BrowseEntry; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      onDoubleClick={onOpen}
      className="text-left flex items-center gap-2 px-2.5 py-1.5 text-xs rounded hover:bg-brand-50 group transition"
    >
      {entry.is_drive ? (
        <HardDrive className="h-3.5 w-3.5 text-brand-500 flex-shrink-0" />
      ) : (
        <Folder className="h-3.5 w-3.5 text-warning flex-shrink-0 group-hover:text-brand-500" />
      )}
      <span className="truncate flex-1" title={entry.path}>{entry.name}</span>
      <ChevronRight className="h-3 w-3 text-muted/40 opacity-0 group-hover:opacity-100 transition" />
    </button>
  );
}

function makeBreadcrumbs(currentPath: string, platform: string): { label: string; path: string }[] {
  if (!currentPath) return [];
  const parts: { label: string; path: string }[] = [];
  // Windows: "C:\Users\bcankara"
  // Unix: "/home/user"
  let buf = "";
  const sep = platform === "windows" ? "\\" : "/";
  const segments = currentPath.split(/[\\/]+/).filter(Boolean);
  for (const seg of segments) {
    buf = buf ? (buf.endsWith(":") ? buf + sep + seg : buf + sep + seg) : (platform === "unix" ? "/" + seg : seg);
    parts.push({ label: seg, path: buf });
  }
  return parts;
}
