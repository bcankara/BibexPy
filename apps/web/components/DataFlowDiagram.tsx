"use client";
import { useEffect, useRef, useState } from "react";
import { api, BASE, type ReportFlow } from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import { Download, GitBranch } from "lucide-react";
import { useI18n, useT } from "@/lib/i18n";

/**
 * Veri akış şeması — audit log'dan otomatik (api.reportFlow).
 * SVG tamamen self-contained çizilir (inline renk/font, CSS sınıfı yok) ki
 * indirilen SVG/PNG makalede olduğu gibi kullanılabilsin.
 *
 * İndirme seçenekleri: SVG (vektör), PNG (300 DPI — pHYs chunk'ı enjekte
 * edilmiş gerçek DPI metadata'sı ile) ve sunucuda üretilen vektörel PDF.
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

// ── PNG 300 DPI: pHYs chunk enjeksiyonu ────────────────────────────────
// Canvas'ın ürettiği PNG'de DPI metadata'sı yoktur; dergiler dosyanın
// kendisinden 300 DPI okumak ister. pHYs chunk'ı (piksel/metre) IHDR'den
// hemen sonra yazılır: 300 DPI = 300 / 0.0254 ≈ 11811 piksel/metre.

const PNG_DPI = 300;
const PNG_PPM = 11811;               // 300 DPI'ın piksel/metre karşılığı
const PNG_SIG = [137, 80, 78, 71, 13, 10, 26, 10];

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunkType(view: DataView, pos: number): string {
  return String.fromCharCode(
    view.getUint8(pos), view.getUint8(pos + 1), view.getUint8(pos + 2), view.getUint8(pos + 3),
  );
}

/** PNG byte'larına 300 DPI pHYs chunk'ı ekler (varsa eskisini değiştirir). */
function injectPhys(buffer: ArrayBuffer, ppm = PNG_PPM): Uint8Array {
  const src = new Uint8Array(buffer);
  for (let i = 0; i < PNG_SIG.length; i++) {
    if (src[i] !== PNG_SIG[i]) return src;      // PNG değil → dokunma
  }
  const view = new DataView(buffer);
  const ihdrLen = view.getUint32(8);            // IHDR data uzunluğu (big-endian)
  const insertAt = 8 + 12 + ihdrLen;            // imza + (len+type+data+crc)
  if (insertAt > src.length) return src;

  // IDAT'a kadar tara; mevcut bir pHYs varsa çıkarılacak aralığı belirle
  let dropAt = -1;
  let dropLen = 0;
  let pos = insertAt;
  while (pos + 8 <= src.length) {
    const len = view.getUint32(pos);
    const type = chunkType(view, pos + 4);
    if (type === "IDAT" || type === "IEND") break;
    if (type === "pHYs") { dropAt = pos; dropLen = 12 + len; break; }
    pos += 12 + len;
  }

  const chunk = new Uint8Array(21);             // 4 len + 4 type + 9 data + 4 crc
  const cv = new DataView(chunk.buffer);
  cv.setUint32(0, 9);
  chunk[4] = 0x70; chunk[5] = 0x48; chunk[6] = 0x59; chunk[7] = 0x73; // "pHYs"
  cv.setUint32(8, ppm);                         // x ekseni: piksel/metre
  cv.setUint32(12, ppm);                        // y ekseni: piksel/metre
  chunk[16] = 1;                                // birim = metre
  cv.setUint32(17, crc32(chunk.subarray(4, 17)));  // CRC: type + data

  let rest: Uint8Array;
  if (dropAt >= 0) {
    const head = src.subarray(insertAt, dropAt);
    const tail = src.subarray(dropAt + dropLen);
    rest = new Uint8Array(head.length + tail.length);
    rest.set(head, 0);
    rest.set(tail, head.length);
  } else {
    rest = src.subarray(insertAt);
  }

  const out = new Uint8Array(insertAt + chunk.length + rest.length);
  out.set(src.subarray(0, insertAt), 0);
  out.set(chunk, insertAt);
  out.set(rest, insertAt + chunk.length);
  return out;
}

function saveBlob(blob: Blob, filename: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function DataFlowDiagram({ projectId }: { projectId: string }) {
  const t = useT();
  const { locale } = useI18n();
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

    if (fmt === "svg") {
      const xml = new XMLSerializer().serializeToString(node);
      saveBlob(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }), "bibexpy_data_flow.svg");
      return;
    }

    // 300 DPI: CSS px 96/inch kabul edilir → ölçek = 300/96
    const scale = PNG_DPI / 96;
    const pxW = Math.round(W * scale);
    const pxH = Math.round(totalH * scale);

    // Rasterizasyon hedef çözünürlükte olsun diye SVG'nin width/height
    // öznitelikleri hedef piksel boyutuna çekilir (viewBox aynı kalır).
    const clone = node.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("width", String(pxW));
    clone.setAttribute("height", String(pxH));
    const xml = new XMLSerializer().serializeToString(clone);
    const url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));

    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = pxW;
      canvas.height = pxH;
      const ctx = canvas.getContext("2d");
      if (!ctx) { URL.revokeObjectURL(url); return; }
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, pxW, pxH);
      ctx.drawImage(img, 0, 0, pxW, pxH);
      URL.revokeObjectURL(url);
      canvas.toBlob((png) => {
        if (!png) return;
        // pHYs chunk'ı ekle → dosya gerçekten 300 DPI olarak okunur
        png.arrayBuffer().then((buf) => {
          const bytes = injectPhys(buf);
          saveBlob(new Blob([bytes], { type: "image/png" }), "bibexpy_data_flow_300dpi.png");
        }).catch(() => saveBlob(png, "bibexpy_data_flow_300dpi.png"));
      }, "image/png");
    };
    img.onerror = () => URL.revokeObjectURL(url);
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
          <Download className="h-3.5 w-3.5" /> PNG · 300 DPI
        </button>
        <a href={`${BASE}/projects/${projectId}/report/flow.pdf?lang=${locale}`}
          target="_blank" rel="noreferrer"
          className="text-xs font-semibold px-2.5 py-1 rounded-md border border-border bg-white hover:border-brand-400 text-muted hover:text-brand-700 flex items-center gap-1.5 transition">
          <Download className="h-3.5 w-3.5" /> PDF
        </a>
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
