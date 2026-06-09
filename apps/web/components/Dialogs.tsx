"use client";
import {
  createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, Info, X, HelpCircle } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

/**
 * Uygulama-içi onay (confirm) ve bildirim (toast) sistemi.
 * Native `window.confirm` / `window.alert` yerine tasarımlı bileşenler.
 *
 * Kullanım:
 *   const confirm = useConfirm();
 *   if (!(await confirm({ message: "...", tone: "danger" }))) return;
 *
 *   const toast = useToast();
 *   toast("Kaydedildi", { tone: "success" });
 */

type Tone = "default" | "danger" | "success" | "warning" | "info";

type ConfirmOptions = {
  title?: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};

type ToastTone = "success" | "danger" | "warning" | "info";
type ToastItem = { id: number; message: string; title?: string; tone: ToastTone };

type DialogCtx = {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  toast: (message: string, opts?: { title?: string; tone?: ToastTone; duration?: number }) => void;
};

const Ctx = createContext<DialogCtx | null>(null);

export function useConfirm() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useConfirm must be used within <DialogProvider>");
  return c.confirm;
}
export function useToast() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useToast must be used within <DialogProvider>");
  return c.toast;
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [confirmState, setConfirmState] =
    useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  useEffect(() => setMounted(true), []);

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => setConfirmState({ ...opts, resolve }));
  }, []);

  const closeConfirm = useCallback((result: boolean) => {
    setConfirmState((cur) => {
      cur?.resolve(result);
      return null;
    });
  }, []);

  const toast = useCallback(
    (message: string, opts?: { title?: string; tone?: ToastTone; duration?: number }) => {
      const id = ++idRef.current;
      const tone = opts?.tone ?? "info";
      setToasts((prev) => [...prev, { id, message, title: opts?.title, tone }]);
      const duration = opts?.duration ?? (tone === "danger" ? 7000 : 4500);
      setTimeout(() => setToasts((p) => p.filter((x) => x.id !== id)), duration);
    },
    [],
  );

  const dismissToast = useCallback((id: number) => {
    setToasts((p) => p.filter((x) => x.id !== id));
  }, []);

  return (
    <Ctx.Provider value={{ confirm, toast }}>
      {children}
      {mounted && confirmState &&
        createPortal(<ConfirmModal state={confirmState} onClose={closeConfirm} />, document.body)}
      {mounted &&
        createPortal(<ToastStack toasts={toasts} onDismiss={dismissToast} />, document.body)}
    </Ctx.Provider>
  );
}

/* ───────────────────── Confirm modal ───────────────────── */

function ConfirmModal({
  state, onClose,
}: {
  state: ConfirmOptions & { resolve: (v: boolean) => void };
  onClose: (v: boolean) => void;
}) {
  const t = useT();
  const danger = state.tone === "danger";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(false);
      if (e.key === "Enter") onClose(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const title = state.title ?? t("common.confirmTitle");
  const cancelLabel = state.cancelLabel ?? t("common.cancel");
  const confirmLabel = state.confirmLabel ?? (danger ? t("common.delete") : t("common.confirm"));

  return (
    <>
      <div className="fixed inset-0 bg-ink/40 backdrop-blur-sm z-[80]" onClick={() => onClose(false)} />
      <div className="fixed inset-0 z-[90] flex items-center justify-center p-4 pointer-events-none">
        <div
          role="dialog"
          aria-modal="true"
          className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden pointer-events-auto animate-in"
        >
          <div className={cn(
            "px-5 py-3.5 border-b flex items-center gap-2.5",
            danger ? "bg-danger-soft/50 border-danger/20" : "bg-brand-50/60 border-brand-100",
          )}>
            <span className={cn(
              "w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0",
              danger ? "bg-danger-soft text-danger" : "bg-brand-100 text-brand-600",
            )}>
              {danger ? <AlertTriangle className="h-4 w-4" /> : <HelpCircle className="h-4 w-4" />}
            </span>
            <h3 className="font-semibold text-sm text-ink flex-1">{title}</h3>
            <button onClick={() => onClose(false)} className="text-muted hover:text-ink p-1 rounded">
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="px-5 py-4 space-y-2">
            <p className="text-sm text-ink whitespace-pre-line leading-relaxed">{state.message}</p>
            {state.detail && (
              <p className="text-xs text-muted whitespace-pre-line bg-bg-soft rounded-lg px-3 py-2">{state.detail}</p>
            )}
          </div>

          <div className="px-5 py-3 bg-bg-soft/40 border-t border-border flex items-center justify-end gap-2">
            <button
              onClick={() => onClose(false)}
              className="px-4 py-2 rounded-lg text-sm font-medium text-muted hover:text-ink hover:bg-bg-soft transition"
            >
              {cancelLabel}
            </button>
            <button
              autoFocus
              onClick={() => onClose(true)}
              className={cn(
                "px-4 py-2 rounded-lg text-sm font-semibold text-white shadow-soft transition",
                danger ? "bg-danger hover:bg-red-600" : "bg-brand-500 hover:bg-brand-600",
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/* ───────────────────── Toast stack ───────────────────── */

const TOAST_STYLE: Record<ToastTone, { icon: ReactNode; ring: string; iconColor: string }> = {
  success: { icon: <CheckCircle2 className="h-4 w-4" />, ring: "border-success/30", iconColor: "text-emerald-600" },
  danger:  { icon: <AlertTriangle className="h-4 w-4" />, ring: "border-danger/30",  iconColor: "text-danger" },
  warning: { icon: <AlertTriangle className="h-4 w-4" />, ring: "border-warning/40", iconColor: "text-warning" },
  info:    { icon: <Info className="h-4 w-4" />,          ring: "border-info/30",    iconColor: "text-blue-600" },
};

function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 w-[min(92vw,22rem)] pointer-events-none">
      {toasts.map((tt) => {
        const s = TOAST_STYLE[tt.tone];
        return (
          <div
            key={tt.id}
            className={cn(
              "pointer-events-auto bg-white rounded-xl border shadow-card px-3.5 py-2.5 flex items-start gap-2.5 animate-in",
              s.ring,
            )}
          >
            <span className={cn("mt-0.5 flex-shrink-0", s.iconColor)}>{s.icon}</span>
            <div className="flex-1 min-w-0">
              {tt.title && <div className="text-sm font-semibold text-ink">{tt.title}</div>}
              <div className="text-xs text-muted whitespace-pre-line break-words leading-relaxed">{tt.message}</div>
            </div>
            <button onClick={() => onDismiss(tt.id)} className="text-muted/60 hover:text-ink p-0.5 flex-shrink-0">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
