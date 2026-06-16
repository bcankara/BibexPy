"""String similarity helpers for author name disambiguation.

Pure-Python utilities with no external dependencies:
- normalize_name: ASCII-fold and lowercase a name into a single-spaced form.
- jaro / jaro_winkler: compute string similarity scores in the range [0, 1].
- name_initials: split a bibliographic author name (surname first) into
  surname and initials.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_name(s: str) -> str:
    """ASCII transliteration + lower + tek boşluk."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    match_dist = max(len(s1), len(s2)) // 2 - 1
    if match_dist < 0:
        match_dist = 0
    s1_matches = [False] * len(s1)
    s2_matches = [False] * len(s2)
    matches = 0
    for i, c in enumerate(s1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len(s2))
        for j in range(start, end):
            if s2_matches[j] or s2[j] != c:
                continue
            s1_matches[i] = s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(len(s1)):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            t += 1
        k += 1
    t /= 2
    return (matches / len(s1) + matches / len(s2) + (matches - t) / matches) / 3


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Jaro-Winkler benzerlik [0, 1]. Önek eşleşmesine bonus verir."""
    j = jaro(s1, s2)
    prefix = 0
    for c1, c2 in zip(s1[:4], s2[:4]):
        if c1 == c2:
            prefix += 1
        else:
            break
    return j + prefix * p * (1 - j)


def name_initials(full: str) -> tuple[str, str]:
    """Bibliyometrik yazar adından (soyad ÖNCE) soyad + baş harfler.

        'Smith, John A'   -> ('smith', 'ja')
        'Smith J A'       -> ('smith', 'ja')
        'van der Berg, J' -> ('van der berg', 'j')

    NOT: WoS/Scopus AU alanı soyadı başa yazar. Eski 'en uzun token = soyad'
    sezgisi 'Jones, Robert' gibi adlarda (ad, soyaddan uzun) soyadı yanlış
    seçip aynı kişiyi farklı bloklara dağıtıyordu — düzeltildi.
    """
    raw = str(full or "")
    if "," in raw:
        # "Soyad, Ad ..." — virgülden öncesi soyad, sonrası baş harf kaynağı
        surname_part, _, given_part = raw.partition(",")
        surname = normalize_name(surname_part)
        initials = "".join(t[0] for t in normalize_name(given_part).split() if t)
        if surname:
            return surname, initials
    # Virgül yok: "Soyad Baş-harfler" — ilk token soyad, kalanlar baş harf
    parts = normalize_name(raw).split()
    if not parts:
        return "", ""
    return parts[0], "".join(p[0] for p in parts[1:] if p)
