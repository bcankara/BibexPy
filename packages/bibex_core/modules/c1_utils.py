"""Helpers for parsing C1 address fields and standardizing countries and parent institutions.

Key functions:
  - canonical_country: maps a country-name variant to its canonical English form (e.g. USA -> United States)
  - parse_c1_address: extracts the parent institution and country from a single address component
  - split_c1_addresses: splits a multi-author C1 field on ';' and strips the "[author]" prefix
  - extract_country_from_c1 / rollup_c1_to_parent: record-level batch helpers
"""

from __future__ import annotations

import re

# ── Ülke varyant -> kanonik (anahtarlar küçük harf). WoS (USA, PEOPLES R CHINA,
#    ENGLAND...) + Scopus (United States, China...) varyantlarını kapsar. ──
COUNTRY_VARIANTS: dict[str, str] = {
    "usa": "United States", "u.s.a.": "United States", "u.s.a": "United States",
    "us": "United States", "u.s.": "United States", "u s a": "United States",
    "united states of america": "United States", "america": "United States",
    "united states": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "northern ireland": "United Kingdom", "north ireland": "United Kingdom",
    "united kingdom": "United Kingdom",
    "peoples r china": "China", "p r china": "China", "prc": "China",
    "peoples republic of china": "China", "china": "China", "mainland china": "China",
    "south korea": "South Korea", "korea": "South Korea", "republic of korea": "South Korea",
    "korea rep": "South Korea", "korea (south)": "South Korea",
    "north korea": "North Korea", "dprk": "North Korea",
    "russia": "Russia", "russian federation": "Russia", "ussr": "Russia",
    "turkiye": "Turkey", "türkiye": "Turkey", "turkey": "Turkey",
    "iran": "Iran", "islamic republic of iran": "Iran",
    "czechia": "Czech Republic", "czech republic": "Czech Republic",
    "netherlands": "Netherlands", "the netherlands": "Netherlands", "holland": "Netherlands",
    "uae": "United Arab Emirates", "u arab emirates": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "germany": "Germany", "deutschland": "Germany", "fed rep ger": "Germany",
    "viet nam": "Vietnam", "vietnam": "Vietnam",
    "taiwan": "Taiwan", "rep of china": "Taiwan",
    "hong kong": "Hong Kong", "macau": "Macau", "macao": "Macau",
    "saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia",
    "brasil": "Brazil", "brazil": "Brazil",
    "espana": "Spain", "españa": "Spain", "spain": "Spain",
    "cote d'ivoire": "Ivory Coast", "ivory coast": "Ivory Coast",
}

# Halihazırda kanonik kabul edilen ülke adları (değişiklik gerekmez) — varyant
# değerleri + yaygın tek-formlu ülkeler.
CANONICAL_COUNTRIES: set[str] = set(COUNTRY_VARIANTS.values()) | {
    "France", "Italy", "Japan", "Canada", "Australia", "India", "Mexico",
    "Poland", "Sweden", "Norway", "Denmark", "Finland", "Belgium", "Austria",
    "Switzerland", "Portugal", "Greece", "Ireland", "Israel", "Egypt", "Pakistan",
    "Indonesia", "Malaysia", "Thailand", "Singapore", "Philippines", "Argentina",
    "Chile", "Colombia", "Peru", "South Africa", "Nigeria", "Kenya", "Morocco",
    "Romania", "Hungary", "Ukraine", "Bulgaria", "Croatia", "Serbia", "Slovenia",
    "Slovakia", "Lithuania", "Latvia", "Estonia", "Qatar", "Kuwait", "Jordan",
    "Lebanon", "Iraq", "New Zealand", "Bangladesh", "Sri Lanka", "Nepal",
}
_CANON_LOWER = {c.lower(): c for c in CANONICAL_COUNTRIES}

# Üst kurum anahtar kelimeleri (varlığı "kurum" işareti)
# Üst kurum anahtar kelimeleri — WoS kısaltmaları dahil (Inst, Hosp, Ctr, Coll...)
_ORG_KW = (
    "univ", "institut", "inst", "college", "coll", "school", "hospital", "hosp",
    "clinic", "center", "centre", "ctr", "laborator", " lab", "academy", "acad",
    "polytechnic", "polytech", "hochschule", "universidad", "universita",
    "université", "ministry", "council", "foundation", "fdn", "national", "natl",
    "research", "tech", "sch", "klinik", "spital",
)
# Alt-birim işaretleri (kurum DEĞİL — tercih edilmez)
_SUBUNIT_KW = (
    "depart", "dept", "division", " div ", "faculty", " fac ", "school of",
    "section", "chair", "unit", "program", "laboratory of",
)
_POSTAL_RE = re.compile(r"\d{4,6}")
_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}


def _norm_country_token(token: str) -> str:
    t = str(token or "").strip().lower().rstrip(".")
    t = re.sub(r"\s+", " ", t)
    return t


def canonical_country(token: str) -> str | None:
    """Ülke varyantını kanonik İngilizce ada eşle. Bilinmiyorsa None (Tier 2)."""
    t = _norm_country_token(token)
    if not t:
        return None
    if t in COUNTRY_VARIANTS:
        return COUNTRY_VARIANTS[t]
    if t in _CANON_LOWER:
        return _CANON_LOWER[t]
    return None


# ── ISO 3166-1 alpha-2 KODU -> kanonik İngilizce ad. OpenAlex `country_code`
#    (US, GB, CN, IN...) -> canonical_country ile aynı adlar (tutarlılık için). ──
COUNTRY_CODE_TO_NAME: dict[str, str] = {
    "US": "United States", "GB": "United Kingdom", "CN": "China", "KR": "South Korea",
    "KP": "North Korea", "RU": "Russia", "TR": "Turkey", "IR": "Iran",
    "CZ": "Czech Republic", "NL": "Netherlands", "AE": "United Arab Emirates",
    "DE": "Germany", "VN": "Vietnam", "TW": "Taiwan", "HK": "Hong Kong", "MO": "Macau",
    "SA": "Saudi Arabia", "BR": "Brazil", "ES": "Spain", "CI": "Ivory Coast",
    "FR": "France", "IT": "Italy", "JP": "Japan", "CA": "Canada", "AU": "Australia",
    "IN": "India", "MX": "Mexico", "PL": "Poland", "SE": "Sweden", "NO": "Norway",
    "DK": "Denmark", "FI": "Finland", "BE": "Belgium", "AT": "Austria",
    "CH": "Switzerland", "PT": "Portugal", "GR": "Greece", "IE": "Ireland",
    "IL": "Israel", "EG": "Egypt", "PK": "Pakistan", "ID": "Indonesia",
    "MY": "Malaysia", "TH": "Thailand", "SG": "Singapore", "PH": "Philippines",
    "AR": "Argentina", "CL": "Chile", "CO": "Colombia", "PE": "Peru",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "MA": "Morocco",
    "RO": "Romania", "HU": "Hungary", "UA": "Ukraine", "BG": "Bulgaria",
    "HR": "Croatia", "RS": "Serbia", "SI": "Slovenia", "SK": "Slovakia",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia", "QA": "Qatar",
    "KW": "Kuwait", "JO": "Jordan", "LB": "Lebanon", "IQ": "Iraq",
    "NZ": "New Zealand", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal",
    "LU": "Luxembourg", "IS": "Iceland", "CY": "Cyprus", "MT": "Malta",
    "EC": "Ecuador", "UY": "Uruguay", "VE": "Venezuela", "CU": "Cuba",
    "CR": "Costa Rica", "PA": "Panama", "BO": "Bolivia", "PY": "Paraguay",
    "TN": "Tunisia", "DZ": "Algeria", "ET": "Ethiopia", "GH": "Ghana",
    "TZ": "Tanzania", "UG": "Uganda", "CM": "Cameroon", "SN": "Senegal",
    "OM": "Oman", "BH": "Bahrain", "YE": "Yemen", "SY": "Syria",
    "AF": "Afghanistan", "KZ": "Kazakhstan", "UZ": "Uzbekistan", "AZ": "Azerbaijan",
    "GE": "Georgia", "AM": "Armenia", "BY": "Belarus", "MD": "Moldova",
    "MK": "North Macedonia", "AL": "Albania", "BA": "Bosnia and Herzegovina",
    "MM": "Myanmar", "KH": "Cambodia", "LA": "Laos", "MN": "Mongolia",
    "BN": "Brunei", "FJ": "Fiji", "PG": "Papua New Guinea", "ZW": "Zimbabwe",
    "ZM": "Zambia", "BW": "Botswana",
}


def country_from_code(cc: str) -> str | None:
    """ISO 3166-1 alpha-2 ülke kodunu (US, GB, CN...) kanonik İngilizce ada eşle."""
    return COUNTRY_CODE_TO_NAME.get(str(cc or "").strip().upper())


def _looks_geographic(part: str) -> bool:
    p = part.strip()
    if not p:
        return True
    if _POSTAL_RE.search(p):
        return True
    toks = p.replace(",", " ").split()
    if any(tk.upper() in _US_STATES for tk in toks):
        return True
    return False


# Net kurum kelimeleri: bir token bunlarla BAŞLIYORSA kurum say (universit*, institut*,
# hospital*, college*, ...). Uzun ve ayırt edici oldukları için ön-ek eşleşmesi güvenli.
_ORG_KW_PREFIX = (
    "univ", "institut", "college", "hospital", "clinic", "center", "centre",
    "laborator", "academ", "polytech", "hochschule", "universidad", "universita",
    "ministr", "council", "foundation", "klinik",
)
# Kısaltmalar / kısa riskli kelimeler: yalnız TAM token eşleşmeli (yer adlarını
# yanlışlıkla kurum saymamak için: 'tech'≠'Techny', 'research'≠'Research Triangle').
# NOT: 'research'/'national'/'tech' tek başına kurum işareti DEĞİL (yer adı olabilir:
# 'Research Triangle Park', 'National City'); listeye alınmadı. 'Research Inst' yine
# 'inst' ile, 'Natl Univ' 'univ' ile yakalanır.
_ORG_KW_EXACT = (
    "inst", "coll", "hosp", "ctr", "lab", "acad", "natl", "fdn", "sch", "dept",
    "polytechnic", "school", "laboratory", "spital",
)


def _is_org(part: str) -> bool:
    """Bileşen bir üst kurum mu? Net kelimeler ön-ekle, kısa/riskli kelimeler yalnız
    TAM token ile eşleşir. Böylece 'Research Triangle Park' (şehir), 'Techny' (yer)
    gibi adresler yanlışlıkla kurum sayılmaz."""
    toks = re.findall(r"[a-zçğıöşü]+", part.lower())
    if not toks:
        return False
    tokset = set(toks)
    if tokset & set(_ORG_KW_EXACT):
        return True
    return any(tok.startswith(pfx) for tok in toks for pfx in _ORG_KW_PREFIX)


def _is_subunit(part: str) -> bool:
    low = part.lower()
    return any(kw in low for kw in _SUBUNIT_KW)


def split_c1_addresses(value: str) -> list[str]:
    """Çok-yazarlı C1'i adres bileşenlerine böl; '[yazarlar]' parantezini soy.

    ';' ile böler AMA köşeli parantez [ ... ] içindeki ';' korunur (yazar listesi
    "[Smith, J; Doe, A]" bölünmemeli).
    """
    raw = str(value or "")
    if not raw.strip() or raw.strip().upper() == "NAN":
        return []
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in raw:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ";" and depth == 0:
            chunks.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        chunks.append("".join(buf))
    out: list[str] = []
    for chunk in chunks:
        c = re.sub(r"^\s*\[[^\]]*\]\s*", "", chunk).strip()  # [Author; Author] kaldır
        if c:
            out.append(c)
    return out


def parse_c1_address(addr: str) -> dict:
    """Tek adres bileşeninden üst kurum + ülke çıkar.

    Dönüş: {raw, institution, country, stripped}. institution/country None olabilir.
    """
    raw = str(addr or "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    country = None
    if parts:
        c = canonical_country(parts[-1])
        if c:
            country = c
            parts = parts[:-1]
    # sondaki şehir/eyalet/posta bileşenlerini soy
    while parts and _looks_geographic(parts[-1]):
        parts.pop()

    institution = None
    # 1) kurum anahtarı olan + alt-birim olmayan en sağdaki parça
    for p in reversed(parts):
        if _is_org(p) and not _is_subunit(p):
            institution = p
            break
    # 2) kurum anahtarı olan herhangi bir parça
    if institution is None:
        for p in reversed(parts):
            if _is_org(p):
                institution = p
                break
    # 3) Kurum anahtarı hiç yoksa TAHMİN ETME (şehir seçme riski) — None bırak;
    #    bu adres Tier 2 (LLM) / elle düzeltmeye kalır, yanlış üst-kurum yazılmaz.

    return {"raw": raw, "institution": institution, "country": country, "stripped": parts}


def extract_country_from_c1(value: str) -> list[str]:
    """Bir C1 hücresindeki tüm ham ülke tokenlerini döndür (kanonikleştirmeden)."""
    out: list[str] = []
    for addr in split_c1_addresses(value):
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if parts:
            out.append(parts[-1])
    return out


def rollup_c1_to_parent(value: str) -> str:
    """C1'in her adres bileşenini üst kuruma indir; çok-yazar yapısını koru."""
    rolled: list[str] = []
    for addr in split_c1_addresses(value):
        info = parse_c1_address(addr)
        inst = info["institution"]
        country = info["country"]
        if inst and country:
            rolled.append(f"{inst}, {country}")
        elif inst:
            rolled.append(inst)
        else:
            rolled.append(addr)
    # benzersizleştir (sırayı koru)
    seen: set[str] = set()
    uniq = [x for x in rolled if not (x in seen or seen.add(x))]
    return "; ".join(uniq)


def _split_keep(value: str) -> list[str]:
    """';' ile böl (parantez-farkındalıklı) AMA '[yazar]' parantezini KORU.

    replace_* fonksiyonları için: orijinal yapı (yazar parantezi dahil) bozulmamalı.
    """
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in str(value or ""):
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ";" and depth == 0:
            chunks.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        chunks.append("".join(buf))
    return chunks


def replace_country_in_c1(value: str, mapping: dict) -> str:
    """C1'deki her adresin ÜLKE bileşenini (en sağdaki virgül parçası) kanonikle değiştir.

    mapping: {küçük-harf-varyant: kanonik}. Yazar parantezi ve diğer bileşenler korunur.
    """
    if not str(value or "").strip():
        return value
    out: list[str] = []
    for chunk in _split_keep(value):
        parts = chunk.split(",")
        if parts:
            last = parts[-1].strip()
            canon = mapping.get(last.lower())
            if canon and last and last.lower() != canon.lower():
                parts[-1] = " " + canon
        out.append(",".join(parts))
    return ";".join(out)


def append_country_to_c1(value: str, append_map: dict) -> str:
    """C1'deki ülke İÇERMEYEN adreslerin sonuna ülkeyi EKLE (replace değil).

    append_map: {son-bileşen-küçük-harf: kanonik-ülke}. Bir adresin son virgül
    bileşeni append_map'te VE zaten bir ülke DEĞİLSE (canonical_country None ise),
    adresin sonuna ", <ülke>" eklenir. Kurum/şehir korunur, yalnız ülke eklenir.

    Kullanım: LLM bir tokenin ülke OLMADIĞINI ama ülkesini bildiğini söylediğinde
    (is_country=0). Böylece kurum adı yok edilmez; eksik ülke doldurulur.
    """
    if not str(value or "").strip():
        return value
    out: list[str] = []
    for chunk in _split_keep(value):
        # '[yazar]' parantezini eşleştirme için geçici soy (çıktıda korunur);
        # extract_country_from_c1 da parantezi soyduğu için anahtarlar böyle eşleşir.
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        addr = (m.group(2) or "").strip()
        parts = addr.split(",")
        last = parts[-1].strip() if parts else ""
        country = append_map.get(last.lower())
        # Zaten ülkeyse dokunma (replace yolu halleder); değilse sona ekle.
        if country and last and canonical_country(last) is None:
            chunk = chunk.rstrip() + ", " + country
        out.append(chunk)
    return ";".join(out)


def replace_affiliation_in_c1(value: str, variant_to_canonical: dict) -> str:
    """C1'deki affiliation bileşenlerini kanonik forma indir (affiliation apply).

    variant_to_canonical: {varyant_affiliation: kanonik}. Bileşen-TAM eşleşme yapar
    (substring değil); '[yazar]' paranteziyle ve diğer ';'/',' bileşenleriyle çakışmaz.
    Eşleşme önce tam ';'-bileşeni, sonra ','-alt-bileşeni üzerinde denenir; ikisi de
    boşluk/büyük-küçük harf duyarsız karşılaştırılır.
    """
    if not str(value or "").strip():
        return value
    # Normalize edilmiş varyant -> kanonik (boşluk-collapse + lower)
    norm_map = {re.sub(r"\s+", " ", str(k).strip()).lower(): v for k, v in variant_to_canonical.items()}

    def _canon(token: str):
        return norm_map.get(re.sub(r"\s+", " ", str(token).strip()).lower())

    out: list[str] = []
    for chunk in _split_keep(value):
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        prefix = m.group(1) or ""
        addr = (m.group(2) or "").strip()
        # 1) Tüm adres tam eşleşiyor mu?
        canon = _canon(addr)
        if canon:
            out.append(prefix + canon)
            continue
        # 2) Adresin virgülle ayrılmış bileşenlerinden biri tam eşleşiyor mu?
        parts = addr.split(",")
        replaced = False
        for i, part in enumerate(parts):
            c = _canon(part)
            if c:
                parts[i] = (" " if i > 0 and part.startswith(" ") else "") + c
                replaced = True
        out.append(prefix + ",".join(parts) if replaced else chunk)
    return ";".join(out)


def replace_org_in_c1(value: str, variant_to_canonical: dict) -> str:
    """C1'deki adresleri üst kurumla değiştir (org rollup apply).

    variant_to_canonical: {tam_adres_varyantı: kanonik_üst_kurum} ('[yazar]' soyulmuş hâli).
    Eşleşen adres bileşeni kanonikle değiştirilir; '[yazar]' parantezi korunur.
    """
    if not str(value or "").strip():
        return value
    # Boşluk/büyük-küçük harf toleranslı eşleme: LLM, adres metnini hafifçe
    # değiştirebilir (fazladan boşluk, harf büyüklüğü). replace_affiliation_in_c1
    # ile aynı normalizasyon deseni.
    norm_map = {re.sub(r"\s+", " ", str(k).strip()).lower(): v for k, v in variant_to_canonical.items()}
    out: list[str] = []
    for chunk in _split_keep(value):
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        prefix = m.group(1) or ""
        addr = (m.group(2) or "").strip()
        canon = norm_map.get(re.sub(r"\s+", " ", addr).lower())
        out.append(prefix + canon if canon else chunk)
    return ";".join(out)
