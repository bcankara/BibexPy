"""Deterministic candidate-group generation for entity disambiguation.

Builds blocked, tiered merge/split candidates before any LLM arbitration:
author name blocking, author splitting, affiliation and organization
clustering, and country-name standardization.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Iterable, Optional

import pandas as pd

from .similarity import jaro_winkler, name_initials, normalize_name


# Kooperatif iptal: ağır builder'lar bir thread'de (asyncio.to_thread) çalışır.
# task.cancel() thread'i ÖLDÜREMEZ — bu yüzden builder, döngü içinde periyodik
# olarak `should_cancel()` kontrol eder ve iptalde erken çıkar. Böylece orphan
# thread paylaşılan havuzu (ve uygulamayı) kilitlemez. _CANCEL_EVERY: kontrol
# sıklığı (her N satır/blok), çağrı maliyetini ihmal edilebilir tutar.
CancelCheck = Optional[Callable[[], bool]]
_CANCEL_EVERY = 256


def _is_cancelled(should_cancel: CancelCheck, counter: int) -> bool:
    return bool(should_cancel) and (counter % _CANCEL_EVERY == 0) and should_cancel()


def _split_authors(value: str) -> list[str]:
    return [a.strip() for a in re.split(r";|\band\b", str(value or "")) if a.strip()]


# Ayrıştırma (split) eki: sistemin kendi koyduğu "(b)", "(c)" ... veya "(7)" işareti.
# Bu ek, daha önce FARKLI kişi olarak ayrılmış bir yazarı belirtir — birleştirme
# adayı olarak görülmemelidir (yoksa scan, kendi split'ini geri alır). Hem tek harf
# (b-z) hem de sayısal (build_author_splits'in 8+ grupta ürettiği "(7)" gibi) ekleri kapsar.
_SPLIT_SUFFIX_RE = re.compile(r"\s*\((?:[b-z]|\d+)\)\s*$", re.IGNORECASE)


def _split_suffix(name: str) -> str:
    """İsimdeki ayrıştırma ekini döndür ('b', 'c', '7' ... ) ya da boş ('' = düz/asıl)."""
    m = _SPLIT_SUFFIX_RE.search(str(name or ""))
    return m.group(0).strip().strip("()").lower() if m else ""


def _base_name(name: str) -> str:
    """Ayrıştırma eki olmadan normalize isim ('LIU L (b)' -> 'liu l').

    Kümeleme/benzerlik için kullanılan KANONİK norm budur: '(b)' eki norm'a
    karışmaz, böylece 'LIU L (b)' yapay olarak 'LIU LEI'ye benzemez (cross-base
    sızıntısı önlenir) ve gerçek kök isimle aynı bloğa düşer.
    """
    return normalize_name(_SPLIT_SUFFIX_RE.sub("", str(name or "")))


def _split_affs(value: str) -> list[str]:
    parts = []
    for chunk in str(value or "").split(";"):
        chunk = re.sub(r"\[.*?\]", "", chunk).strip()
        if chunk:
            parts.append(chunk)
    return parts


# ───────────── Tier sınıflandırma yardımcıları ─────────────
# 3 katmanlı güven (Fellegi-Sunter 1969 match / possible-match / non-match):
#   Tier 1 (auto)  — çok yüksek benzerlik + paylaşılan bağlam → LLM'siz birleştir
#   Tier 2 (llm)   — orta benzerlik / belirsiz → LLM hakemliğine gönder
#   Tier 3 (skip)  — tek varyant / uzak → dokunma

# Alan/kategori kolonları — kurumdan daha kararlı sinyal (kişi kurum değiştirir
# ama alanını nadiren değiştirir). WC: WoS kategorileri, SC: araştırma alanları,
# DE: yazar anahtar kelimeleri, ID: Keywords Plus.
_FIELD_COLS = ("WC", "SC", "DE", "ID")


def _row_fields(row) -> set[str]:
    """Bir kaydın alan/kategori/anahtar-kelime token kümesi (normalize).
    pandas NaN / boş / 'nan' değerleri sahte token üretmesin diye elenir.
    """
    out: set[str] = set()
    for col in _FIELD_COLS:
        val = row.get(col, "")
        if val is None:
            continue
        sval = str(val).strip()
        if not sval or sval.lower() == "nan":
            continue
        for tok in sval.split(";"):
            t = tok.strip().lower()
            if t and t != "nan":
                out.add(t)
    return out


def _field_signal(members: list[dict]) -> bool | None:
    """Alan sinyali (kurum yerine konu/kategori bazlı):
        None  → hiç alan verisi yok → sinyal yok, isim benzerliğine güven
        True  → en az iki mention ortak alan/kategori paylaşıyor → aynı kişi lehine
        False → alan verisi var ama örtüşme yok → farklı alanlar, aynı-isim/farklı-kişi şüphesi
    """
    sets = [m.get("fields") or set() for m in members]
    if not any(sets):
        return None
    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            if sets[i] & sets[j]:
                return True
    return False


def _greedy_cluster(members: list[dict], threshold: float) -> list[list[dict]]:
    """Mention'ları norm benzerliğine göre transitif öbekle (≥ threshold → aynı küme).

    İstisna: split eki ('(b)', '(c)' ...) TAŞIYAN bir mention, sistemin daha önce
    bilerek ayırdığı bir kimliktir → hiçbir birleştirme kümesine sokulmaz (tek başına
    kalır, sonra <2 varyant filtresiyle elenir). Aksi halde scan kendi split'ini geri
    alır (örn. 'LIU L (b)'yi 'LIU LEI' ile birleştirmeye çalışır).
    """
    clustered: list[list[dict]] = []
    for m in members:
        if m.get("suffix"):
            clustered.append([m])  # ayrıştırılmış kimlik → izole, asla birleşme adayı
            continue
        placed = False
        for cluster in clustered:
            # Hedef küme bir split-kimliği içeriyorsa oraya da koyma.
            if any(x.get("suffix") for x in cluster):
                continue
            if any(jaro_winkler(m["norm"], x["norm"]) >= threshold for x in cluster):
                cluster.append(m)
                placed = True
                break
        if not placed:
            clustered.append([m])
    return clustered


def _min_pairwise_jw(norms: set[str]) -> float:
    """Bir varyant kümesindeki en düşük ikili Jaro-Winkler (küme tutarlılığı ölçüsü)."""
    items = sorted(norms)
    if len(items) < 2:
        return 1.0
    lo = 1.0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            lo = min(lo, jaro_winkler(items[i], items[j]))
    return lo


def _candidate(key: str, ci: int, cluster: list[dict], variant_field: str, tier: int) -> dict:
    """Bir öbeği UI/LLM şemasına uygun aday objesine dönüştür."""
    names = sorted({m["name"] for m in cluster})
    cand = {
        "id": f"{key}_{ci}",
        variant_field: names,
        "affiliations": sorted({a for m in cluster for a in m.get("affiliations", [])})[:10],
        "coauthors": sorted({c for m in cluster for c in m.get("coauthors", [])})[:10],
        "records": sorted({m["record"] for m in cluster})[:50],
        "tier": tier,
    }
    years = sorted({m["year"] for m in cluster if m.get("year")})
    cand["year_range"] = [years[0], years[-1]] if years else []
    return cand


def build_author_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Yazar adlarını blok blok grupla; saf eşik mantığıyla tier'lara ayır.

    3 katman (Fellegi-Sunter match / possible-match / non-match):
      • Tier 1 (auto)  — isim benzerliği ≥ auto_threshold (0.95) VE alan çelişkisi yok
                         → LLM'siz birleştir önerisi (block["auto"])
      • Tier 2 (llm)   — 0.75–0.95 arası, ya da ≥0.95 ama alanlar farklı
                         → LLM hakemliğine (block["members"])
      • Tier 3 (skip)  — tek varyant / < sim_threshold → dokunma

    Alan sinyali: WC/SC/DE/ID (konu/kategori). Kurumdan daha kararlı çünkü kişi
    kurum değiştirir ama alanını nadiren değiştirir. `mode` parametresi geriye
    uyumluluk için durur, davranışı etkilemez.

    Dönüş: bloklar; her blokta opsiyonel "auto" (Tier 1) ve "members" (Tier 2).
    """
    if "AU" not in df.columns:
        return []

    # surname+initial -> list of (record_idx, name, affiliations, coauthors, year)
    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        authors = _split_authors(row.get("AU", ""))
        if not authors:
            continue
        affs = _split_affs(row.get("C1", "") or row.get("C1raw", ""))
        coauthors = authors
        year = str(row.get("PY", "") or "").strip()
        fields = _row_fields(row)  # WC/SC/DE/ID — alan sinyali (kurumdan kararlı)
        for au in authors:
            # Blok anahtarı + benzerlik, split eki ('(b)') OLMADAN hesaplanır; aksi
            # halde '(b)' hem initials'ı ('l b') hem norm'u kirletip yapay benzerlik
            # üretir ve cross-base sızıntısına yol açar.
            base_au = _SPLIT_SUFFIX_RE.sub("", au).strip()
            surname, initials = name_initials(base_au)
            if not surname:
                continue
            key = f"{surname}_{initials[:1]}" if initials else surname
            blocks[key].append({
                "record": int(idx),
                "name": au,
                "norm": normalize_name(base_au),  # suffix'siz kanonik norm
                "base": _base_name(au),           # = norm; çatışma tespiti için açıkça
                "suffix": _split_suffix(au),      # '' / 'b' / 'c' / '7' ...
                "affiliations": affs,
                "coauthors": [c for c in coauthors if c != au],
                "year": year,
                "fields": fields,
            })

    # Her blok: sim_threshold (0.75) bileşenleri → saf eşik mantığı
    #   bileşen ≥2 varyant + min ikili JW ≥ auto_threshold (0.95) → Tier 1 (otomatik)
    #   bileşen ≥2 varyant + 0.75–0.95 arası                      → Tier 2 (LLM)
    #   tek varyant / <0.75                                       → Tier 3 (atla)
    out: list[dict] = []
    for key, members in blocks.items():
        # Blok döngüsü: bloklar az ama her biri ağır (_greedy_cluster O(n²)) →
        # her blokta kontrol et (modulo'suz); iptalde o ana kadarki sonucu döndür.
        if should_cancel and should_cancel():
            return out
        if len({m["norm"] for m in members}) < 2:
            continue  # birleştirilecek varyant yok

        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2:
                continue  # tek varyantlı bileşen → birleştirme yok (Tier 3)
            if _min_pairwise_jw(comp_norms) >= auto_threshold and _field_signal(comp) is not False:
                # İsimler ≥0.95 VE (ortak alan VAR ya da alan verisi YOK) → otomatik (Tier 1).
                # Alan verisi var ama örtüşmüyorsa (_field_signal False) → AI'a düşer.
                auto_clusters.append(_candidate(key, ci, comp, "name_variants", tier=1))
            else:
                llm_members.append(_candidate(key, ci, comp, "name_variants", tier=2))

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out


def _field_components(recs: list[dict]) -> list[list[dict]]:
    """Kayıtları 'ortak kategori paylaşıyorlar' bağına göre bileşenlere ayır.
    Bir kayıt birden çok bileşene değiyorsa o bileşenler birleşir (transitif).
    Sonuç bileşenler birbirinden alan bakımından TAM AYRIK olur.
    """
    comps: list[list[dict]] = []
    for r in recs:
        rf = r["fields"]
        hit = [ci for ci, comp in enumerate(comps) if any(rf & x["fields"] for x in comp)]
        if not hit:
            comps.append([r])
        else:
            first = hit[0]
            comps[first].append(r)
            for ci in sorted(hit[1:], reverse=True):
                comps[first].extend(comps[ci])
                del comps[ci]
    return comps


def build_author_splits(df: pd.DataFrame, should_cancel: CancelCheck = None) -> list[dict]:
    """Yazar AYRIŞTIRMA: aynı yazılışa sahip bir isim, alan bakımından ayrık
    kayıt gruplarına bölünüyorsa muhtemelen FARKLI kişilerdir → ayrıştırma önerisi.

    Tier 1 (net): tüm kayıtların alan verisi var ve 2+ tam-ayrık gruba bölünüyor
                  → "şu alanlar şuradan ayrı" gerekçesiyle ayrıştır (en büyük grup
                  düz kalır, diğerleri (b) (c)... eki alır).
    Tier 2 (sınırda): bazı kayıtlarda alan verisi yok → AI'a sor (ayır / bırak).
    Aksi (tek grup / zincirlenmiş ortak alan) → ayrıştırma yok.

    Dönüş: split önerileri; her biri {split_id, name, norm, tier, groups[], reason}.
    """
    if "AU" not in df.columns:
        return []
    mentions: dict[str, list[dict]] = defaultdict(list)
    name_of: dict[str, str] = {}
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        authors = _split_authors(row.get("AU", ""))
        if not authors:
            continue
        fields = _row_fields(row)
        for au in authors:
            # Zaten ayrıştırılmış isim ('ZIPF A (b)') TEKRAR split adayı olmamalı —
            # yoksa her scan onu yeniden bölüp 'ZIPF A (b) (b)' üretir (idempotent değil).
            if _split_suffix(au):
                continue
            nm = normalize_name(au)
            if not nm:
                continue
            mentions[nm].append({"record": int(idx), "fields": fields})
            name_of.setdefault(nm, au)

    suffixes = ["", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)"]
    out: list[dict] = []
    for norm, recs in mentions.items():
        if should_cancel and should_cancel():
            return out
        if len(recs) < 2:
            continue  # tek kayıt → ayrıştıracak bir şey yok
        with_fields = [r for r in recs if r["fields"]]
        unknown = [r for r in recs if not r["fields"]]
        if len(with_fields) < 2:
            continue  # alan sinyali yok → karar verilemez (atla)

        comps = _field_components(with_fields)
        if len(comps) < 2:
            continue  # tek (zincirlenmiş) grup → aynı kişi, ayrıştırma yok

        comps.sort(key=len, reverse=True)  # en büyük grup düz kalır
        groups = []
        for gi, comp in enumerate(comps):
            gfields = sorted({f for r in comp for f in r["fields"]})
            groups.append({
                "records": sorted({r["record"] for r in comp}),
                "fields": gfields[:8],
                "suffix": suffixes[gi] if gi < len(suffixes) else f"({gi})",
            })
        field_summ = " | ".join("[" + ", ".join(g["fields"][:3]) + "]" for g in groups)
        reason = f"Same name, {len(groups)} field-disjoint groups: {field_summ}"
        tier = 1
        if unknown:
            tier = 2
            reason += f"; also {len(unknown)} record(s) without field info"
        out.append({
            "split_id": f"{norm.replace(' ', '_')}_{len(out)}",
            "name": name_of[norm],
            "norm": norm,
            "tier": tier,
            "groups": groups,
            "reason": reason,
        })
    return out


def build_affiliation_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Affiliation varyantlarını ilk-anlamlı-token bazlı blocking. Bkz. build_author_blocks."""
    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    # canonical form -> list of variants
    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        affs = _split_affs(row.get("C1", "") or row.get("C1raw", ""))
        for aff in affs:
            norm = normalize_name(aff)
            if not norm:
                continue
            # ilk anlamlı token (stopword olmayan)
            tokens = [t for t in norm.split() if t not in {"the", "of", "and", "for", "univ", "university"}]
            key = tokens[0] if tokens else norm[:5]
            blocks[key].append({
                "record": int(idx),
                "raw": aff,
                "norm": norm,
            })

    out: list[dict] = []
    for key, members in blocks.items():
        if should_cancel and should_cancel():
            return out
        if len({m["norm"] for m in members}) < 2:
            continue

        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2:
                continue  # tek varyant → birleştirme yok (Tier 3)
            cand = {
                "id": f"{key}_{ci}",
                "variants": sorted({m["raw"] for m in comp}),
                "records": sorted({m["record"] for m in comp})[:100],
            }
            # Affiliation: yüksek string benzerliği tek başına güçlü sinyal (bağlam yok).
            if _min_pairwise_jw(comp_norms) >= auto_threshold:
                cand["tier"] = 1
                auto_clusters.append(cand)
            else:
                cand["tier"] = 2
                llm_members.append(cand)

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out


def build_country_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Ülke adı standartlaştırma — C1'in ülke bileşeni.

    Tier 1 (deterministik): ISO sözlüğü varyantı kanonik ada eşler (USA -> United States).
    Tier 2 (LLM): sözlükte olmayan tokenler benzerlik bazlı kümelenip hakeme sorulur.
    """
    from bibex_core.modules.c1_utils import canonical_country, extract_country_from_c1, _is_org

    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    resolved: dict[str, dict] = {}     # canonical -> {variants, records}
    unresolved: dict[str, set] = {}    # token(lower) -> records
    unresolved_disp: dict[str, str] = {}
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        val = row.get("C1") or row.get("C1raw") or ""
        for tok in extract_country_from_c1(val):
            t = tok.strip()
            if not t:
                continue
            canon = canonical_country(t)
            if canon:
                r = resolved.setdefault(canon, {"variants": set(), "records": set()})
                r["variants"].add(t)
                r["records"].add(int(idx))
            else:
                # Açıkça-ülke-olmayan tokenleri (kurum / posta kodu) LLM'e GÖNDERME —
                # kurum=ülke karmasını kaynağında önler. Şehirler LLM'e gider ve prompt
                # onları 'uncertain'e atar (önerilmez). Eksik ülke ayrı API eylemiyle tamamlanır.
                if _is_org(t) or any(ch.isdigit() for ch in t):
                    continue
                key = t.lower()
                unresolved.setdefault(key, set()).add(int(idx))
                unresolved_disp.setdefault(key, t)

    auto_clusters: list[dict] = []
    for canon, data in resolved.items():
        variants = sorted(data["variants"])
        if all(v.strip().lower() == canon.lower() for v in variants):
            continue  # zaten kanonik — değişiklik yok
        auto_clusters.append({
            "id": f"country_{re.sub(r'[^a-z0-9]+', '_', canon.lower()).strip('_')}",
            "variants": variants,
            "canonical_name": canon,
            "records": sorted(data["records"])[:1000],
            "tier": 1,
        })

    if should_cancel and should_cancel():
        return []
    members = [
        {"record": min(recs), "raw": unresolved_disp[k], "norm": normalize_name(unresolved_disp[k]), "_recs": recs}
        for k, recs in unresolved.items()
    ]
    llm_members: list[dict] = []
    for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
        recs: set[int] = set()
        variants_u: set[str] = set()
        for m in comp:
            variants_u.add(m["raw"])
            recs |= m["_recs"]
        llm_members.append({
            "id": f"country_u_{ci}",
            "variants": sorted(variants_u),
            "records": sorted(recs)[:1000],
            "tier": 2,
        })

    block: dict = {"key": "country"}
    if auto_clusters:
        block["auto"] = auto_clusters
    if llm_members:
        block["members"] = llm_members
    return [block] if (auto_clusters or llm_members) else []


def build_org_rollup(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Affiliation → üst kurum toplama.

    Her C1 adresini ayrıştırıp üst kurumu çıkarır; aynı kuruma ait adres varyantlarını
    kümeler. Tier 1: tek-net adres veya yüksek kurum-adı benzerliği. Tier 2: sınırda
    varyantlar LLM hakemliğine. Apply eşleşen TAM adresi üst kurumla DEĞİŞTİRİR.
    """
    from collections import Counter
    from bibex_core.modules.c1_utils import parse_c1_address, split_c1_addresses

    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        val = row.get("C1") or row.get("C1raw") or ""
        for addr in split_c1_addresses(val):
            info = parse_c1_address(addr)
            inst = info["institution"]
            if not inst:
                continue  # parse edilemedi → atla (yanlış rollup yazma)
            norm = normalize_name(inst)
            if not norm:
                continue
            toks = [t for t in norm.split() if t not in {"the", "of", "and", "for", "univ", "university"}]
            key = toks[0] if toks else norm[:5]
            blocks[key].append({"record": int(idx), "raw": addr, "norm": norm, "parent": inst})

    out: list[dict] = []
    for key, members in blocks.items():
        if should_cancel and should_cancel():
            return out
        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            parent = Counter(m["parent"] for m in comp).most_common(1)[0][0]
            variants = sorted({m["raw"] for m in comp})
            if len(variants) == 1 and variants[0].strip() == parent.strip():
                continue  # tek adres zaten üst kuruma eşit
            cand = {
                "id": f"org_{key}_{ci}",
                "variants": variants,           # tam orijinal adresler (apply bunları değiştirir)
                "canonical_name": parent,       # önerilen üst kurum
                "records": sorted({m["record"] for m in comp})[:1000],
            }
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2 or _min_pairwise_jw(comp_norms) >= auto_threshold:
                cand["tier"] = 1
                auto_clusters.append(cand)
            else:
                cand["tier"] = 2
                llm_members.append(cand)

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out
