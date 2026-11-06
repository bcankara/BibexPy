"""Helpers for parsing C1 address fields and standardizing countries and parent institutions.

Key functions:
  - canonical_country: maps a country-name variant to its canonical English form (e.g. USA -> United States)
  - parse_c1_address: extracts the parent institution and country from a single address component
  - split_c1_addresses: splits a multi-author C1 field on ';' and strips the "[author]" prefix
  - extract_country_from_c1 / rollup_c1_to_parent: record-level batch helpers
"""

from __future__ import annotations

import re

# ── Country variant -> canonical (keys are lowercase). Covers WoS variants
#    (USA, PEOPLES R CHINA, ENGLAND...) + Scopus variants (United States, China...). ──
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

# Country names already considered canonical (no change needed) — variant
# values + common single-form countries.
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

# Parent-institution keywords (their presence marks an "institution")
# Parent-institution keywords — including WoS abbreviations (Inst, Hosp, Ctr, Coll...)
_ORG_KW = (
    "univ", "institut", "inst", "college", "coll", "school", "hospital", "hosp",
    "clinic", "center", "centre", "ctr", "laborator", " lab", "academy", "acad",
    "polytechnic", "polytech", "hochschule", "universidad", "universita",
    "université", "ministry", "council", "foundation", "fdn", "national", "natl",
    "research", "tech", "sch", "klinik", "spital",
)
# Sub-unit markers (NOT an institution — not preferred)
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
    """Map a country variant to its canonical English name. Returns None if unknown (Tier 2)."""
    t = _norm_country_token(token)
    if not t:
        return None
    if t in COUNTRY_VARIANTS:
        return COUNTRY_VARIANTS[t]
    if t in _CANON_LOWER:
        return _CANON_LOWER[t]
    return None


# ── ISO 3166-1 alpha-2 CODE -> canonical English name. OpenAlex `country_code`
#    (US, GB, CN, IN...) -> same names as canonical_country (for consistency). ──
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
    """Map an ISO 3166-1 alpha-2 country code (US, GB, CN...) to its canonical English name."""
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


# Unambiguous institution words: if a token STARTS WITH one of these, count it as an
# institution (universit*, institut*, hospital*, college*, ...). Prefix matching is
# safe because they are long and distinctive.
_ORG_KW_PREFIX = (
    "univ", "institut", "college", "hospital", "clinic", "center", "centre",
    "laborator", "academ", "polytech", "hochschule", "universidad", "universita",
    "ministr", "council", "foundation", "klinik",
)
# Abbreviations / short risky words: only a FULL token may match (so that place names
# are not mistaken for institutions: 'tech'≠'Techny', 'research'≠'Research Triangle').
# NOTE: 'research'/'national'/'tech' alone are NOT institution markers (they may be
# place names: 'Research Triangle Park', 'National City'); they were not added to the
# list. 'Research Inst' is still caught via 'inst', 'Natl Univ' via 'univ'.
_ORG_KW_EXACT = (
    "inst", "coll", "hosp", "ctr", "lab", "acad", "natl", "fdn", "sch", "dept",
    "polytechnic", "school", "laboratory", "spital",
)


def _is_org(part: str) -> bool:
    """Is the component a parent institution? Unambiguous words match by prefix, while
    short/risky words match only as a FULL token. This way addresses such as
    'Research Triangle Park' (a city) or 'Techny' (a place) are not mistaken for
    institutions."""
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
    """Split a multi-author C1 into address components; strip the '[authors]' bracket.

    Splits on ';' BUT a ';' inside square brackets [ ... ] is preserved (the author list
    "[Smith, J; Doe, A]" must not be split).
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
        c = re.sub(r"^\s*\[[^\]]*\]\s*", "", chunk).strip()  # remove [Author; Author]
        if c:
            out.append(c)
    return out


def parse_c1_address(addr: str) -> dict:
    """Extract the parent institution + country from a single address component.

    Returns: {raw, institution, country, stripped}. institution/country may be None.
    """
    raw = str(addr or "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    country = None
    if parts:
        c = canonical_country(parts[-1])
        if c:
            country = c
            parts = parts[:-1]
    # strip trailing city/state/postal components
    while parts and _looks_geographic(parts[-1]):
        parts.pop()

    institution = None
    # 1) the rightmost part that has an institution keyword and is not a sub-unit
    for p in reversed(parts):
        if _is_org(p) and not _is_subunit(p):
            institution = p
            break
    # 2) any part that has an institution keyword
    if institution is None:
        for p in reversed(parts):
            if _is_org(p):
                institution = p
                break
    # 3) If there is no institution keyword at all, do NOT GUESS (risk of picking the
    #    city) — leave None; this address is left to Tier 2 (LLM) / manual correction,
    #    so no wrong parent institution is written.

    return {"raw": raw, "institution": institution, "country": country, "stripped": parts}


def extract_country_from_c1(value: str) -> list[str]:
    """Return all raw country tokens in a C1 cell (without canonicalizing)."""
    out: list[str] = []
    for addr in split_c1_addresses(value):
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if parts:
            out.append(parts[-1])
    return out


def rollup_c1_to_parent(value: str) -> str:
    """Roll every address component of C1 up to its parent institution; keep the multi-author structure."""
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
    # deduplicate (preserve order)
    seen: set[str] = set()
    uniq = [x for x in rolled if not (x in seen or seen.add(x))]
    return "; ".join(uniq)


def _split_keep(value: str) -> list[str]:
    """Split on ';' (bracket-aware) BUT KEEP the '[author]' bracket.

    For the replace_* functions: the original structure (including the author bracket) must stay intact.
    Lossless: ";".join(_split_keep(value)) == value (a trailing ';' yields a final empty chunk).
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
    # always emit the last chunk, even when empty (trailing ';'), so ";".join(chunks) == value
    chunks.append("".join(buf))
    return chunks


def replace_country_in_c1(value: str, mapping: dict) -> str:
    """Replace the COUNTRY component (the rightmost comma part) of every address in C1 with the canonical one.

    mapping: {lowercase-variant: canonical}. The author bracket and other components are preserved.
    """
    if not str(value or "").strip():
        return value
    out: list[str] = []
    changed = False
    for chunk in _split_keep(value):
        parts = chunk.split(",")
        if parts:
            last = parts[-1].strip()
            canon = mapping.get(last.lower())
            if canon and last and last.lower() != canon.lower():
                parts[-1] = " " + canon
                changed = True
        out.append(",".join(parts))
    return ";".join(out) if changed else value


def append_country_to_c1(value: str, append_map: dict) -> str:
    """APPEND the country to the end of addresses in C1 that do NOT contain a country (not a replace).

    append_map: {last-component-lowercase: canonical-country}. If the last comma
    component of an address is in append_map AND is not already a country
    (i.e. canonical_country is None), ", <country>" is appended to the end of the
    address. The institution/city is preserved; only the country is added.

    Usage: when the LLM says a token is NOT a country but knows its country
    (is_country=0). This way the institution name is not destroyed; the missing
    country is filled in.
    """
    if not str(value or "").strip():
        return value
    out: list[str] = []
    changed = False
    for chunk in _split_keep(value):
        # Temporarily strip the '[author]' bracket for matching (it is kept in the output);
        # extract_country_from_c1 also strips the bracket, so the keys match this way.
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        addr = (m.group(2) or "").strip()
        parts = addr.split(",")
        last = parts[-1].strip() if parts else ""
        country = append_map.get(last.lower())
        # If it is already a country, leave it alone (the replace path handles it); otherwise append.
        if country and last and canonical_country(last) is None:
            chunk = chunk.rstrip() + ", " + country
            changed = True
        out.append(chunk)
    return ";".join(out) if changed else value


def replace_affiliation_in_c1(value: str, variant_to_canonical: dict) -> str:
    """Reduce the affiliation components in C1 to their canonical form (affiliation apply).

    variant_to_canonical: {variant_affiliation: canonical}. Performs a component-EXACT
    match (not substring); it does not clash with the '[author]' bracket or with other
    ';'/',' components. The match is tried first on the full ';'-component, then on the
    ','-sub-component; both comparisons are whitespace/case insensitive.
    """
    if not str(value or "").strip():
        return value
    # Normalized variant -> canonical (whitespace-collapse + lower)
    norm_map = {re.sub(r"\s+", " ", str(k).strip()).lower(): v for k, v in variant_to_canonical.items()}

    def _canon(token: str):
        return norm_map.get(re.sub(r"\s+", " ", str(token).strip()).lower())

    out: list[str] = []
    changed = False
    for chunk in _split_keep(value):
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        prefix = m.group(1) or ""
        addr = (m.group(2) or "").strip()
        # 1) Does the whole address match exactly?
        canon = _canon(addr)
        if canon:
            out.append(prefix + canon)
            changed = True
            continue
        # 2) Does one of the address's comma-separated components match exactly?
        parts = addr.split(",")
        replaced = False
        for i, part in enumerate(parts):
            c = _canon(part)
            if c:
                parts[i] = (" " if i > 0 and part.startswith(" ") else "") + c
                replaced = True
        if replaced:
            changed = True
        out.append(prefix + ",".join(parts) if replaced else chunk)
    # nothing replaced: hand back the original cell byte for byte
    return ";".join(out) if changed else value


def replace_org_in_c1(value: str, variant_to_canonical: dict) -> str:
    """Replace addresses in C1 with the parent institution (org rollup apply).

    variant_to_canonical: {full_address_variant: canonical_parent_institution} (with the '[author]' bracket stripped).
    The matching address component is replaced with the canonical one; the '[author]' bracket is preserved.
    """
    if not str(value or "").strip():
        return value
    # Whitespace/case-tolerant matching: the LLM may slightly alter the address text
    # (extra whitespace, letter case). Same normalization pattern as
    # replace_affiliation_in_c1.
    norm_map = {re.sub(r"\s+", " ", str(k).strip()).lower(): v for k, v in variant_to_canonical.items()}
    out: list[str] = []
    changed = False
    for chunk in _split_keep(value):
        m = re.match(r"^(\s*\[[^\]]*\]\s*)?(.*)$", chunk, re.S)
        prefix = m.group(1) or ""
        addr = (m.group(2) or "").strip()
        canon = norm_map.get(re.sub(r"\s+", " ", addr).lower())
        if canon:
            changed = True
        out.append(prefix + canon if canon else chunk)
    return ";".join(out) if changed else value
