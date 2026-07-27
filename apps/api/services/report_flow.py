"""Builds a publication-ready data-flow summary from the audit log.

Derives the record-count pipeline for the ACTIVE analysis — identification
(raw inputs), deduplication (intra-source + cross-source matches with stage
breakdown), uncertain-pair decisions, and every subsequent exclusion step
(permanent filter applies, manual deletions) — ending at the current dataset
size. The Report page renders this as a downloadable flow diagram, so the
manuscript's data-preparation figure comes straight from the recorded
operations.

The same diagram is also rendered server-side as a TRUE VECTOR PDF
(`render_flow_pdf`) for journals that require scalable figures.
"""

from __future__ import annotations

import io
import os
from typing import Any, Optional

from services import analyses, audit


def build_flow(project_id: str) -> dict[str, Any]:
    """Aktif analizin kayıtlı operasyonlarından akış özetini çıkar."""
    aid: Optional[str] = None
    try:
        aid = analyses.get_active_analysis_id(project_id)
    except Exception:
        aid = None

    entries = audit.read(project_id, analysis_id=aid)

    # Son Smart Merge girdisi — akışın kökü
    merge_entry: Optional[dict] = None
    for e in entries:
        if e.get("kind") == "merge" and (e.get("details") or {}).get("method") == "smart":
            merge_entry = e  # sonuncusu kazanır (yeniden merge)

    flow: dict[str, Any] = {
        "analysis_id": aid,
        "has_merge": merge_entry is not None,
        "inputs": None,
        "intra_removed": 0,
        "matched_pairs": 0,
        "stages": {},
        "borderline_total": 0,
        "after_merge": None,
        "steps": [],
        "final_total": None,
    }

    merge_ts = 0.0
    if merge_entry is not None:
        d = merge_entry.get("details") or {}
        wos = int(d.get("wos_input") or 0)
        scp = int(d.get("scopus_input") or 0)
        flow["inputs"] = {"wos": wos, "scopus": scp, "total": wos + scp}
        flow["intra_removed"] = int(d.get("intra_wos_removed") or 0) + int(d.get("intra_scopus_removed") or 0)
        flow["matched_pairs"] = int(d.get("matched_pairs") or 0)
        flow["stages"] = d.get("match_stages") or {}
        flow["borderline_total"] = int(d.get("borderline_count") or 0)
        flow["after_merge"] = int(d.get("merged_count") or 0)
        merge_ts = float(merge_entry.get("ts") or 0)

    # Merge SONRASI dışlama adımları — kronolojik
    for e in entries:
        ts = float(e.get("ts") or 0)
        if ts <= merge_ts:
            continue
        kind = e.get("kind")
        d = e.get("details") or {}
        if kind == "filter_apply":
            flow["steps"].append({
                "kind": "filter_apply",
                "removed": int(d.get("removed") or 0),
                "after": int(d.get("after")) if d.get("after") is not None else None,
                "criteria": list(d.get("filter_keys") or []),
                "ts": ts,
            })
        elif kind == "records_delete":
            after = (e.get("after") or {}).get("total")
            flow["steps"].append({
                "kind": "records_delete",
                "removed": int(d.get("deleted") or 0),
                "after": int(after) if after is not None else None,
                "criteria": [],
                "ts": ts,
            })
        elif kind == "merge_borderline":
            applied = int(d.get("applied_changes") or 0)
            if applied > 0:
                flow["steps"].append({
                    "kind": "merge_borderline",
                    "removed": applied,
                    "after": None,
                    "criteria": [],
                    "ts": ts,
                })

    # Nihai kayıt sayısı — canlı dataset'ten (audit zinciri eksik olsa da doğru)
    try:
        from services import filter_engine
        flow["final_total"] = int(len(filter_engine.load_merged(project_id)))
    except Exception:
        flow["final_total"] = None

    return flow


# ─────────────────────────────────────────────────────────────────────────
#  Vektörel PDF çıktısı (aynı şema — reportlab.pdfgen ile)
# ─────────────────────────────────────────────────────────────────────────
#
# Geometri ve renkler DataFlowDiagram.tsx ile birebir aynıdır; oradaki SVG
# kullanıcı birimleri (px) burada da kullanılır, sayfaya sığdırmak için tek
# bir uniform ölçek uygulanır. Çizim gerçek vektördür (roundRect/line/path),
# metinler gömülü font ile yazılır — dergiye gönderilecek figür kalitesinde.

_W = 760.0            # şema genişliği
_MAIN_W = 380.0       # ana kutu genişliği
_MAIN_X = 40.0        # ana kutu sol x
_SIDE_W = 260.0       # yan (çıkarma) kutusu genişliği
_SIDE_X = _MAIN_X + _MAIN_W + 40.0
_LINE_H = 17.0
_PAD = 10.0
_GAP = 34.0           # kutular arası dikey boşluk (ok payı)

_NAVY = "#0c2847"
_INK = "#172033"
_MUTED = "#5f6f85"
_LINE = "#c9d6e5"
_WARN_BG = "#fef6e7"
_WARN_BR = "#f0c36d"
_WARN_INK = "#8a5a00"
_OK_BG = "#e8f6f1"
_OK_BR = "#4a9e97"
_OK_INK = "#0b433f"

# Frontend i18n anahtarlarının (report.flow.*) sunucu tarafı karşılığı
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "identification": "Identification (raw input)",
        "sources": "WoS {wos} + Scopus {scopus}",
        "records": "{n} records",
        "dedup": "Deduplication — Smart Merge",
        "pairsMerged": "{n} pairs merged",
        "intraRemoved": "intra-source duplicates: {n}",
        "removed": "−{n} records removed",
        "unique": "Unique record set",
        "borderlineKept": "{n} uncertain pairs kept separate",
        "stepFilter": "Filter applied: {keys}",
        "stepDelete": "Manual record deletion",
        "stepBorderline": "Uncertain pairs accepted (merged)",
        "final": "Final dataset",
    },
    "tr": {
        "identification": "Tanımlama (ham girdi)",
        "sources": "WoS {wos} + Scopus {scopus}",
        "records": "{n} kayıt",
        "dedup": "Tekilleştirme — Smart Merge",
        "pairsMerged": "{n} çift birleştirildi",
        "intraRemoved": "kaynak-içi kopya: {n}",
        "removed": "−{n} kayıt çıkarıldı",
        "unique": "Benzersiz kayıt kümesi",
        "borderlineKept": "{n} belirsiz çift ayrı tutuldu",
        "stepFilter": "Filtre uygulandı: {keys}",
        "stepDelete": "Elle kayıt silme",
        "stepBorderline": "Belirsiz çift onayı (birleştirildi)",
        "final": "Nihai veri seti",
    },
}

# Unicode TTF adayları (regular, bold) — ilk bulunan kazanır
_FONT_CANDIDATES: list[tuple[str, str]] = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]

# TTF yoksa Helvetica WinAnsi'ye düşülür — Türkçe (ve U+2212 eksi) harfleri
# render edemediği için ASCII'ye çevrilir ki hiçbir karakter bozulmasın.
_ASCII_MAP = str.maketrans({
    "ğ": "g", "Ğ": "G", "ş": "s", "Ş": "S", "ı": "i", "İ": "I",
    "ö": "o", "Ö": "O", "ü": "u", "Ü": "U", "ç": "c", "Ç": "C",
    "−": "-",
})

_FONT_CACHE: Optional[tuple[str, str, bool]] = None


def _fonts() -> tuple[str, str, bool]:
    """(regular, bold, unicode_ok) — bir kez kaydedip önbellekle."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    result = ("Helvetica", "Helvetica-Bold", False)
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        for reg_path, bold_path in _FONT_CANDIDATES:
            if not os.path.exists(reg_path):
                continue
            try:
                pdfmetrics.registerFont(TTFont("FlowFont", reg_path))
            except Exception:
                continue
            bold_name = "FlowFont"
            if bold_path and os.path.exists(bold_path):
                try:
                    pdfmetrics.registerFont(TTFont("FlowFont-Bold", bold_path))
                    bold_name = "FlowFont-Bold"
                except Exception:
                    bold_name = "FlowFont"
            result = ("FlowFont", bold_name, True)
            break
    except Exception:
        result = ("Helvetica", "Helvetica-Bold", False)
    _FONT_CACHE = result
    return result


def _fmt_n(value: Any, lang: str) -> str:
    """Binlik ayraçlı sayı — en: 1,972 / tr: 1.972."""
    try:
        n = int(value or 0)
    except Exception:
        n = 0
    s = f"{n:,}"
    return s.replace(",", ".") if lang == "tr" else s


def _box_h(lines: list[str]) -> float:
    return len(lines) * _LINE_H + _PAD * 2


def _build_boxes(flow: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    """DataFlowDiagram.tsx'teki kutu modelinin birebir karşılığı."""
    L = _LABELS.get(lang, _LABELS["en"])
    inputs = flow.get("inputs") or {}
    boxes: list[dict[str, Any]] = []

    boxes.append({"lines": [
        L["identification"],
        L["sources"].format(wos=_fmt_n(inputs.get("wos"), lang),
                            scopus=_fmt_n(inputs.get("scopus"), lang)),
        L["records"].format(n=_fmt_n(inputs.get("total"), lang)),
    ], "side": [], "final": False})

    stages = flow.get("stages") or {}
    stage_parts = [f"{k}: {_fmt_n(v, lang)}" for k, v in stages.items()]
    matched = int(flow.get("matched_pairs") or 0)
    intra = int(flow.get("intra_removed") or 0)
    dedup_side = [L["removed"].format(n=_fmt_n(matched + intra, lang))]
    if intra > 0:
        dedup_side.append(L["intraRemoved"].format(n=_fmt_n(intra, lang)))
    dedup_lines = [L["dedup"], L["pairsMerged"].format(n=_fmt_n(matched, lang))]
    if stage_parts:
        dedup_lines.append("  ·  ".join(stage_parts))
    boxes.append({"lines": dedup_lines, "side": dedup_side, "final": False})

    after_merge = flow.get("after_merge") or 0
    unique_lines = [L["unique"], L["records"].format(n=_fmt_n(after_merge, lang))]
    if int(flow.get("borderline_total") or 0) > 0:
        unique_lines.append(L["borderlineKept"].format(n=_fmt_n(flow["borderline_total"], lang)))
    boxes.append({"lines": unique_lines, "side": [], "final": False})

    running = int(after_merge)
    for s in flow.get("steps") or []:
        removed = int(s.get("removed") or 0)
        after = s.get("after")
        running = int(after) if after is not None else max(0, running - removed)
        kind = s.get("kind")
        if kind == "filter_apply":
            label = L["stepFilter"].format(keys=", ".join(s.get("criteria") or []) or "—")
        elif kind == "records_delete":
            label = L["stepDelete"]
        else:
            label = L["stepBorderline"]
        boxes.append({
            "lines": [L["records"].format(n=_fmt_n(running, lang))],
            "side": [L["removed"].format(n=_fmt_n(removed, lang)), label],
            "final": False,
        })

    final_total = flow.get("final_total")
    boxes.append({"lines": [
        L["final"],
        L["records"].format(n=_fmt_n(running if final_total is None else final_total, lang)),
    ], "side": [], "final": True})
    return boxes


def render_flow_pdf(project_id: str, lang: str = "en") -> bytes:
    """Veri akış şemasını tek sayfalık, vektörel bir PDF olarak üret.

    Frontend'deki SVG ile aynı geometri/renk; A4 dikey, 2 cm kenar boşluğu,
    yatayda ortalanmış ve tek sayfaya sığacak şekilde uniform ölçeklenmiş.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas as pdfcanvas

    lang = "tr" if str(lang).lower().startswith("tr") else "en"
    flow = build_flow(project_id)
    if not flow.get("has_merge"):
        raise ValueError("no_merge_for_flow")

    font_reg, font_bold, unicode_ok = _fonts()

    def txt(s: str) -> str:
        s = str(s)
        return s if unicode_ok else s.translate(_ASCII_MAP)

    boxes = _build_boxes(flow, lang)

    # ── Geometri (SVG ile aynı akış) ──
    y = 16.0
    placed: list[dict[str, Any]] = []
    for b in boxes:
        h = _box_h(b["lines"])
        placed.append({**b, "top": y, "h": h})
        y += h + _GAP
    total_h = y - _GAP + 16.0

    # ── Sayfa yerleşimi ──
    page_w, page_h = A4
    margin = 2 * cm
    avail_w = page_w - 2 * margin
    avail_h = page_h - 2 * margin
    scale = min(avail_w / _W, avail_h / total_h, 1.0)
    x_off = margin + (avail_w - _W * scale) / 2.0
    y_off = page_h - margin  # şemanın üst kenarı

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle("BibexPy — Data Flow Diagram")
    c.setAuthor("BibexPy")
    c.setLineCap(1)
    c.setLineJoin(1)

    c.saveState()
    c.translate(x_off, y_off)
    c.scale(scale, scale)
    # Bundan sonrası SVG kullanıcı birimi; y aşağı doğru arttığı için -y çizilir.

    def draw_centred(s: str, cx: float, y_svg: float, size: float,
                     bold: bool, color: str, max_w: float) -> None:
        """Ortalanmış metin — kutuya sığmazsa punto küçültülür (taşma olmasın)."""
        font = font_bold if bold else font_reg
        s = txt(s)
        try:
            w = pdfmetrics.stringWidth(s, font, size)
        except Exception:
            w = 0.0
        if w > max_w > 0:
            size = max(6.5, size * max_w / w)
        c.setFont(font, size)
        c.setFillColor(HexColor(color))
        c.drawCentredString(cx, -y_svg, s)

    for i, b in enumerate(placed):
        is_final = bool(b["final"])
        top: float = b["top"]
        h: float = b["h"]
        cx = _MAIN_X + _MAIN_W / 2

        # dikey ok — önceki kutudan
        if i > 0:
            prev = placed[i - 1]
            c.setStrokeColor(HexColor(_NAVY))
            c.setLineWidth(1.4)
            c.line(cx, -(prev["top"] + prev["h"]), cx, -(top - 6))
            p = c.beginPath()
            p.moveTo(cx - 4, -(top - 7))
            p.lineTo(cx, -(top - 1))
            p.lineTo(cx + 4, -(top - 7))
            c.drawPath(p, stroke=1, fill=0)

        # ana kutu
        c.setFillColor(HexColor(_OK_BG if is_final else "#ffffff"))
        c.setStrokeColor(HexColor(_OK_BR if is_final else _NAVY))
        c.setLineWidth(1.5)
        c.roundRect(_MAIN_X, -(top + h), _MAIN_W, h, 8, stroke=1, fill=1)
        for li, ln in enumerate(b["lines"]):
            first = li == 0
            draw_centred(
                ln, cx, top + _PAD + _LINE_H * (li + 0.75),
                12.5 if first else 12.0, first,
                (_OK_INK if is_final else _NAVY) if first else _INK,
                _MAIN_W - 16,
            )

        # yan kutu (çıkarılanlar)
        side: list[str] = b["side"] or []
        if side:
            mid = top + h / 2
            sh = _box_h(side)
            s_top = mid - sh / 2
            c.setStrokeColor(HexColor(_LINE))
            c.setLineWidth(1.4)
            c.line(_MAIN_X + _MAIN_W, -mid, _SIDE_X - 6, -mid)
            p = c.beginPath()
            p.moveTo(_SIDE_X - 12, -(mid - 4))
            p.lineTo(_SIDE_X - 5, -mid)
            p.lineTo(_SIDE_X - 12, -(mid + 4))
            c.drawPath(p, stroke=1, fill=0)
            c.setFillColor(HexColor(_WARN_BG))
            c.setStrokeColor(HexColor(_WARN_BR))
            c.setLineWidth(1.3)
            c.roundRect(_SIDE_X, -(s_top + sh), _SIDE_W, sh, 8, stroke=1, fill=1)
            for li, ln in enumerate(side):
                first = li == 0
                draw_centred(
                    ln, _SIDE_X + _SIDE_W / 2,
                    s_top + _PAD + _LINE_H * (li + 0.75),
                    11.5, first, _WARN_INK if first else _MUTED,
                    _SIDE_W - 16,
                )

    c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()
