"use client";
import { useEffect, useRef, useState } from "react";
import { api, type ReportFlow } from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import { Download, GitBranch } from "lucide-react";
import { useT } from "@/lib/i18n";

/**
 * Veri akış şeması — audit log'dan otomatik (api.reportFlow).
 * SVG tamamen self-contained çizilir (inline renk/font, CSS sınıfı yok) ki
 * indirilen SVG/PNG makalede olduğu gibi kullanılabilsin.
 */

const W = 760;                 // SVG genişliği
const MAIN_W = 380;            // ana kutu genişliği
const MAIN_X = 40;             // ana kutu sol x
const SIDE_W = 260;            // yan (çıkarma) kutusu genişliği
const SIDE_X = MAIN_X + MAIN_W + 40;
const LINE_H = 17;
const PAD = 10;
const GAP = 34;                // kutular arası dikey boşluk (ok payı)

const NAVY = "#0c2847";
const INK = "#172033";
const MUTED = "#5f6f85";
const LINE = "#c9d6e5";
const WARN_BG = "#fef6e7";
const WARN_BR = "#f0c36d";
const OK_BG = "#e8f6f1";
const OK_BR = "#4a9e97";

type Box = { lines: string[]; side?: string[]; tone?: "final" };

function boxH(lines: string[]): number {
  return lines.length * LINE_H + PAD * 2;
}

export function DataFlowDiagram({ projectId }: { projectId: string }) {
  const t = useT();
  const [flow, setFlow] = useState<ReportFlow | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    let alive = true;
    api.reportFlow(projectId).then((f) => { if (alive) setFlow(f); }).catch(() => {});
    return () => { alive = false; };
  }, [projectId]);

  if (!flow || !flow.has_merge || !flow.inputs) return null;

  const n = (x: number | null | undefined) => (x ?? 0).toLocaleString();

  // ── Kutu modeli ──
  const boxes: Box[] = [];
  boxes.push({
    lines: [
      t("report.flow.identification"),
      t("report.flow.sources", { wos: n(flow.inputs.wos), scopus: n(flow.inputs.scopus) }),
      t("report.flow.records", { n: n(flow.inputs.total) }),
    ],
  });

  const stageParts = Object.entries(flow.stages).map(([k, v]) => `${k}: ${n(v)}`);
  const dedupSide = [t("report.flow.removed", { n: n(flow.matched_pairs + flow.intra_removed) })];
  if (flow.intra_removed > 0) dedupSide.push(t("report.flow.intraRemoved", { n: n(flow.intra_removed) }));
  boxes.push({
    lines: [
      t("report.flow.dedup"),
      t("report.flow.pairsMerged", { n: n(flow.matched_pairs) }),
      ...(stageParts.length ? [stageParts.join("  ·  ")] : []),
    ],
    side: dedupSide,
  });

  const uniqueLines = [t("report.flow.unique"), t("report.flow.records", { n: n(flow.after_merge) })];
  if (flow.borderline_total > 0) uniqueLines.push(t("report.flow.borderlineKept", { n: n(flow.borderline_total) }));
  boxes.push({ lines: uniqueLines });

  let running = flow.after_merge ?? 0;
  for (const s of flow.steps) {
    running = s.after ?? Math.max(0, running - s.removed);
    const label =
      s.kind === "filter_apply"
        ? t("report.flow.stepFilter", { keys: s.criteria.join(", ") || "—" })
        : s.kind === "records_delete"
          ? t("report.flow.stepDelete")
          : t("report.flow.stepBorderline");
    boxes.push({
      lines: [t("report.flow.records", { n: n(running) })],
      side: [t("report.flow.removed", { n: n(s.removed) }), label],
    });
  }

  boxes.push({
    lines: [t("report.flow.final"), t("report.flow.records", { n: n(flow.final_total ?? running) })],
    tone: "final",
  });

  // ── Geometri ──
  let y = 16;
  const placed = boxes.map((b) => {
    const h = boxH(b.lines);
    const top = y;
    y += h + GAP;
    return { ...b, top, h };
  });
  const totalH = y - GAP + 16;

  function download(fmt: "svg" | "png") {
    const node = svgRef.current;
    if (!node) return;
    const xml = new XMLSerializer().serializeToString(node);
    const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
    if (fmt === "svg") {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "bibexpy_data_flow.svg";
      a.click();
      URL.revokeObjectURL(a.href);
      return;
    }
    const img = new Image();
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = W * 2;
      canvas.height = totalH * 2;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((png) => {
        if (!png) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(png);
        a.download = "bibexpy_data_flow.png";
        a.click();
        URL.revokeObjectURL(a.href);
      });
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }

  const font = "system-ui, -apple-system, 'Segoe UI', sans-serif";

  return (
    <Card>
      <CardHeader>
        <GitBranch className="h-4 w-4 text-brand-600" />
        <h2 className="font-semibold text-sm flex-1">{t("report.flow.title")}</h2>
        <button onClick={() => download("svg")}
          className="text-xs font-semibold px-2.5 py-1 rounded-md border border-border bg-white hover:border-brand-400 text-muted hover:text-brand-700 flex items-center gap-1.5 transition">
          <Download className="h-3.5 w-3.5" /> SVG
        </button>
        <button onClick={() => download("png")}
          className="text-xs font-semibold px-2.5 py-1 rounded-md border border-border bg-white hover:border-brand-400 text-muted hover:text-brand-700 flex items-center gap-1.5 transition">
          <Download className="h-3.5 w-3.5" /> PNG
        </button>
      </CardHeader>
      <CardBody className="space-y-2">
        <p className="text-xs text-muted">{t("report.flow.desc")}</p>
        <div className="overflow-x-auto">
          <svg
            ref={svgRef}
            xmlns="http://www.w3.org/2000/svg"
            viewBox={`0 0 ${W} ${totalH}`}
            width={W}
            height={totalH}
            style={{ maxWidth: "100%", height: "auto" }}
          >
            <rect x={0} y={0} width={W} height={totalH} fill="#ffffff" />
            {placed.map((b, i) => {
              const isFinal = b.tone === "final";
              const cx = MAIN_X + MAIN_W / 2;
              return (
                <g key={i}>
                  {/* dikey ok — önceki kutudan */}
                  {i > 0 && (
                    <g stroke={NAVY} strokeWidth={1.4}>
                      <line x1={cx} y1={placed[i - 1].top + placed[i - 1].h} x2={cx} y2={b.top - 6} />
                      <path d={`M ${cx - 4} ${b.top - 7} L ${cx} ${b.top - 1} L ${cx + 4} ${b.top - 7}`}
                        fill="none" strokeLinecap="round" strokeLinejoin="round" />
                    </g>
                  )}
                  {/* ana kutu */}
                  <rect x={MAIN_X} y={b.top} width={MAIN_W} height={b.h} rx={8}
                    fill={isFinal ? OK_BG : "#ffffff"} stroke={isFinal ? OK_BR : NAVY} strokeWidth={1.5} />
                  {b.lines.map((ln, li) => (
                    <text key={li} x={cx} y={b.top + PAD + LINE_H * (li + 0.75)}
                      textAnchor="middle" fontFamily={font}
                      fontSize={li === 0 ? 12.5 : 12} fontWeight={li === 0 ? 700 : 400}
                      fill={li === 0 ? (isFinal ? "#0b433f" : NAVY) : INK}>
                      {ln}
                    </text>
                  ))}
                  {/* yan kutu (çıkarılanlar) */}
                  {b.side && b.side.length > 0 && (
                    <g>
                      <line x1={MAIN_X + MAIN_W} y1={b.top + b.h / 2} x2={SIDE_X - 6} y2={b.top + b.h / 2}
                        stroke={LINE} strokeWidth={1.4} />
                      <path d={`M ${SIDE_X - 12} ${b.top + b.h / 2 - 4} L ${SIDE_X - 5} ${b.top + b.h / 2} L ${SIDE_X - 12} ${b.top + b.h / 2 + 4}`}
                        fill="none" stroke={LINE} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" />
                      <rect x={SIDE_X} y={b.top + b.h / 2 - boxH(b.side) / 2} width={SIDE_W} height={boxH(b.side)} rx={8}
                        fill={WARN_BG} stroke={WARN_BR} strokeWidth={1.3} />
                      {b.side.map((ln, li) => (
                        <text key={li} x={SIDE_X + SIDE_W / 2}
                          y={b.top + b.h / 2 - boxH(b.side!) / 2 + PAD + LINE_H * (li + 0.75)}
                          textAnchor="middle" fontFamily={font} fontSize={11.5}
                          fontWeight={li === 0 ? 700 : 400} fill={li === 0 ? "#8a5a00" : MUTED}>
                          {ln}
                        </text>
                      ))}
                    </g>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
        <p className="text-[10px] text-muted">{t("report.flow.note")}</p>
      </CardBody>
    </Card>
  );
}
