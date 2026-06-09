import requests
import pandas as pd
import os
import re
import json
import time
import difflib
from functools import lru_cache
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Tüm dış API çağrıları için (connect, read) timeout — tek bir API takılırsa
# tüm enrichment işinin süresiz kilitlenmesini önler.
REQUEST_TIMEOUT = (5, 20)


def _get_with_retry(url: str, *, headers: dict | None = None, attempts: int = 3, **kw):
    """requests.get + zorunlu timeout + 429/503 üstel backoff (Retry-After saygılı)."""
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, **kw)
        except requests.RequestException as e:
            last = e
            time.sleep(min(2 ** i, 8))
            continue
        if r.status_code in (429, 503):
            retry_after = r.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(2 ** i, 8)
            except (TypeError, ValueError):
                delay = min(2 ** i, 8)
            time.sleep(min(delay, 15))
            last = r
            continue
        return r
    if isinstance(last, requests.Response):
        return last
    raise last if last else requests.RequestException(f"request failed: {url}")


# ── Ters DOI arama (eksik DI'yi başlık+yıl+yazardan bul) ────────────────────────
def _norm_title(s) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(s).lower())
    return re.sub(r"\s+", " ", s).strip()


def _title_sim(a, b) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _first_surname(authors) -> str:
    """'ZHANG C; GUO R' / 'Zhang, Chen; ...' -> ilk yazarın soyadı (küçük harf)."""
    if not authors:
        return ""
    first = str(authors).split(";")[0].strip()
    first = first.split(",")[0].strip()
    parts = first.split()
    return (parts[0] if parts else "").lower()


def _verify_candidate(c_title, c_year, c_surname, title, year, surname) -> bool:
    """Dengeli ama KESİN doğrulama: başlık benzerliği >= 0.90, yıl ±1, ilk yazar soyadı.

    Kesinlik (precision) > geri-çağırma: emin olunmayan aday REDDEDİLİR (yanlış DOI
    yazmaktansa boş bırak).
      • Yıl: ikisi de varsa ve parse edilebiliyorsa ±1; parse edilemezse aday REDDEDİLİR
        (sessizce atlanmaz — eski hata: except: pass yılı tamamen devre dışı bırakıyordu).
      • Soyad: ikisi de varsa TAM eşleşmeli (substring değil — 'li' artık 'oliveira'yı
        kabul etmez).
    """
    if _title_sim(c_title, title) < 0.90:
        return False
    if year and c_year:
        try:
            if abs(int(float(str(c_year))) - int(float(str(year)))) > 1:
                return False
        except (TypeError, ValueError):
            return False  # aday yılı yorumlanamıyor → güvenli tarafta kal, reddet
    if surname and c_surname and surname != c_surname:
        return False  # tam eşleşme şart (substring kabul edilmez)
    return True


def _crossref_candidates(title: str, crossref_email: str) -> list:
    out = []
    try:
        params = {"query.bibliographic": title, "rows": 5}
        if crossref_email:
            params["mailto"] = crossref_email
        r = _get_with_retry("https://api.crossref.org/works", params=params)
        if r.ok:
            for it in r.json().get("message", {}).get("items", []):
                ti = (it.get("title") or [""])[0]
                issued = (it.get("issued") or {}).get("date-parts") or [[None]]
                cy = issued[0][0] if issued and issued[0] else None
                au = it.get("author") or []
                csur = (au[0].get("family", "") if au else "").lower()
                out.append((it.get("DOI", ""), ti, cy, csur))
    except Exception:
        pass
    return out


def _openalex_candidates(title: str, year: str, crossref_email: str) -> list:
    out = []
    try:
        flt = f"title.search:{title}"
        if year:
            flt += f",publication_year:{year}"
        params = {"filter": flt, "per_page": 5}
        if crossref_email:
            params["mailto"] = crossref_email
        r = _get_with_retry("https://api.openalex.org/works", params=params)
        if r.ok:
            for w in r.json().get("results", []):
                doi = (w.get("doi") or "").replace("https://doi.org/", "")
                ti = w.get("display_name") or w.get("title") or ""
                cy = w.get("publication_year")
                auth = w.get("authorships") or []
                nm = ((auth[0].get("author") or {}).get("display_name", "") if auth else "")
                csur = nm.split()[-1].lower() if nm else ""
                out.append((doi, ti, cy, csur))
    except Exception:
        pass
    return out


_DOI_LOOKUP_CACHE: dict = {}


def _resolve_doi_cached(title: str, year: str, surname: str, crossref_email: str) -> str:
    key = (title, year, surname, crossref_email)
    if key in _DOI_LOOKUP_CACHE:
        return _DOI_LOOKUP_CACHE[key]
    cands = _crossref_candidates(title, crossref_email) + _openalex_candidates(title, year, crossref_email)
    best_doi, best_sim = "", 0.0
    for doi, c_title, c_year, c_surname in cands:
        if not doi:
            continue
        if not _verify_candidate(c_title, c_year, c_surname, title, year, surname):
            continue
        sim = _title_sim(c_title, title)
        if sim > best_sim:
            best_sim, best_doi = sim, doi
    # YALNIZ başarılı sonucu cache'le — geçici ağ hatası "boş" olarak kalıcı yapışmasın.
    if best_doi:
        _DOI_LOOKUP_CACHE[key] = best_doi
    return best_doi


def resolve_doi(title, authors=None, year=None, *, crossref_email: str | None = None):
    """Eksik DOI'yi başlık+yıl+yazardan ters arar (CrossRef -> OpenAlex), doğrular, döndürür.

    Dengeli eşik: başlık ~0.90, yıl ±1, (varsa) ilk yazar soyadı. Emin değilse None
    döner (asla uydurmaz). Sonuç (title, year, surname, email) ile cache'lenir.
    """
    if not title or not str(title).strip():
        return None
    surname = _first_surname(authors)
    yr = ""
    if year not in (None, ""):
        try:
            yr = str(int(float(str(year))))
        except (TypeError, ValueError):
            yr = ""
    doi = _resolve_doi_cached(str(title).strip()[:300], yr, surname, crossref_email or "")
    return doi or None


def _decode_inverted_abstract(inv: dict) -> str:
    """OpenAlex abstract_inverted_index ({token: [pozisyonlar]}) → düz metin."""
    if not isinstance(inv, dict) or not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for token, idxs in inv.items():
        if isinstance(idxs, list):
            for p in idxs:
                positions.append((int(p), token))
    positions.sort(key=lambda x: x[0])
    return " ".join(tok for _, tok in positions)

def load_api_config() -> dict:
    """Load API configuration from API_config.json.

    Once BIBEXPY_CONFIG_DIR (paket modu, orn. ~/.bibexpy), sonra kaynak-agaci
    konumuna bakar. Bulunamazsa sessizce bos dict doner — v2 sunucusunda API
    anahtarlari env/config.py'den gelir, bu dosya zorunlu degildir.
    """
    candidates = []
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        candidates.append(os.path.join(os.path.expanduser(cfg_dir), 'API_config.json'))
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'API_config.json')
    )
    config_path = next((p for p in candidates if os.path.exists(p)), None)
    if not config_path:
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"\nError loading API configuration: {str(e)}")
        return {}

def get_api_credential(service: str, field: str = 'api_key') -> str:
    """Get API credential for a specific service"""
    config = load_api_config()
    if service in config and field in config[service]:
        return config[service][field]
    return None

@lru_cache(maxsize=4096)
def extract_metadata_from_crossref(doi: str, email: str = None) -> dict:
    """Extract metadata from CrossRef API (Free, no key required)"""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        headers = {
            'User-Agent': f'BibexPy/2.0 (mailto:{email or "info@bibexpy.org"})'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            work = data['message']
            
            # Extract relevant fields
            metadata = {}
            
            # DOI
            metadata['DI'] = work.get('DOI', '')
            
            # Document Type
            if 'type' in work:
                metadata['DT'] = work['type']
            
            # Authors (+ ORCID)
            if 'author' in work:
                authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in work['author']]
                metadata['AU'] = '; '.join(authors)
                # ORCID (OI) — CrossRef'te çoğu zaman boş ama varsa kesin sinyal.
                orcids = []
                for a in work['author']:
                    oid = str(a.get('ORCID') or "").replace("https://orcid.org/", "").replace("http://orcid.org/", "").strip()
                    if oid:
                        nm = f"{a.get('given', '')} {a.get('family', '')}".strip()
                        orcids.append(f"{nm}/{oid}")
                if orcids:
                    metadata['OI'] = '; '.join(orcids)
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title'][0]
            
            # Publication Year
            if 'issued' in work and 'date-parts' in work['issued']:
                metadata['PY'] = work['issued']['date-parts'][0][0]
            
            # Journal/Container Title
            if 'container-title' in work:
                metadata['SO'] = work['container-title'][0]
            
            # Publisher
            if 'publisher' in work:
                metadata['PU'] = work['publisher']
            
            # Publisher Address/Location
            if 'publisher-location' in work:
                metadata['PA'] = work['publisher-location']
            
            # Abstract
            if 'abstract' in work:
                metadata['AB'] = work['abstract']
            
            # ISSN
            if 'ISSN' in work:
                metadata['SN'] = '; '.join(work['ISSN'])
            
            # URL
            if 'URL' in work:
                metadata['UR'] = work['URL']
            
            # Subject -> yalnız anahtar kelime (DE). WC/SC gerçek WoS/Scopus
            # kategorileri DEĞİLDİR; onları ML enrichment doldurur (de-collapse).
            if 'subject' in work and work['subject']:
                metadata['DE'] = '; '.join(work['subject'])
            
            # License
            if 'license' in work and work['license']:
                license_urls = [lic.get('URL', '') for lic in work['license']]
                metadata['LI'] = '; '.join(filter(None, license_urls))
            
            return metadata
    except Exception as e:
        print(f"CrossRef API Error: {str(e)}")
    return {}

def truncate_url(url: str, max_length: int = 2079) -> str:
    """Excel'in URL karakter limitine uygun olarak URL'yi kısalt"""
    if not url or len(url) <= max_length:
        return url
    return url[:max_length-3] + "..."

@lru_cache(maxsize=4096)
def extract_metadata_from_openalex(doi: str, email: str = None) -> dict:
    """Extract metadata from OpenAlex API (Free, no key required)"""
    try:
        url = f"https://api.openalex.org/works/doi:{doi}"
        if email:
            url += f"?mailto={email}"
        response = _get_with_retry(url)
        
        if response.status_code == 200:
            work = response.json()
            
            # Extract relevant fields
            metadata = {}
            
            # DOI
            if 'doi' in work:
                metadata['DI'] = work['doi']
            
            # Document Type
            if 'type' in work:
                metadata['DT'] = work['type']
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            elif 'display_name' in work:
                metadata['TI'] = work['display_name']
            
            # Authors and Institutions
            if 'authorships' in work:
                # Authors
                authors = []
                institutions = []
                author_institutions = []  # For author-institution pairs
                orcids = []          # WoS OI: 'Author/0000-0000-0000-0000'
                researcher_ids = []  # WoS RI: 'Author/<OpenAlex A-ID>' (yedek kimlik)
                rors = []            # benzersiz ROR kurum URI'leri
                countries = []       # ISO ülke kodları (yazar kurumlarından)

                for authorship in work['authorships']:
                    if 'author' in authorship and 'display_name' in authorship['author']:
                        author = authorship['author']
                        author_name = author['display_name']
                        authors.append(author_name)

                        # ORCID (OI) — disambiguation'ın altın standardı
                        orcid = author.get('orcid')
                        if orcid:
                            oid = str(orcid).replace("https://orcid.org/", "").strip()
                            if oid:
                                orcids.append(f"{author_name}/{oid}")
                        # Yazar kimliği (RI yedek) — OpenAlex A-ID
                        aid = author.get('id')
                        if aid:
                            researcher_ids.append(f"{author_name}/{str(aid).replace('https://openalex.org/', '').strip()}")

                        # Get institutions for this author (+ ROR + country)
                        if 'institutions' in authorship:
                            author_insts = []
                            for inst in authorship['institutions']:
                                if 'display_name' in inst:
                                    inst_name = inst['display_name']
                                    institutions.append(inst_name)
                                    author_insts.append(inst_name)
                                if inst.get('ror'):
                                    rors.append(str(inst['ror']).replace("https://ror.org/", "").strip())
                                if inst.get('country_code'):
                                    countries.append(str(inst['country_code']).strip().upper())

                            if author_insts:
                                author_institutions.append(f"{author_name} [{'; '.join(author_insts)}]")
                        # Yazar-seviyesi countries[] (kurum yoksa da gelebilir)
                        for cc in authorship.get('countries', []) or []:
                            if cc:
                                countries.append(str(cc).strip().upper())

                if authors:
                    metadata['AU'] = '; '.join(authors)
                # Unique institutions
                if institutions:
                    metadata['C1'] = '; '.join(list(dict.fromkeys(institutions)))
                # Author-Institution pairs
                if author_institutions:
                    metadata['AF'] = '; '.join(author_institutions)
                # ORCID (OI) + ResearcherID (RI) + ROR + ülke kodları
                if orcids:
                    metadata['OI'] = '; '.join(orcids)
                if researcher_ids:
                    metadata['RI'] = '; '.join(researcher_ids)
                if rors:
                    metadata['ROR'] = '; '.join(dict.fromkeys(rors))
                if countries:
                    metadata['CC'] = '; '.join(dict.fromkeys(countries))

            # Publication Year
            if 'publication_year' in work:
                metadata['PY'] = work['publication_year']
            
            # Journal/Publisher — yeni şema primary_location.source (host_venue deprecated).
            venue = {}
            ploc = work.get('primary_location') or {}
            if isinstance(ploc, dict) and isinstance(ploc.get('source'), dict):
                venue = ploc['source']
            elif isinstance(work.get('host_venue'), dict):
                venue = work['host_venue']
            if venue:
                if venue.get('display_name'):
                    metadata['SO'] = venue['display_name']
                if venue.get('host_organization_name') or venue.get('publisher'):
                    metadata['PU'] = venue.get('host_organization_name') or venue.get('publisher')
                if venue.get('issn_l'):
                    metadata['SN'] = venue['issn_l']
            if isinstance(ploc, dict) and ploc.get('landing_page_url'):
                metadata['UR'] = truncate_url(ploc['landing_page_url'])

            # Abstract — OpenAlex 'abstract_inverted_index' verir; düz 'abstract' yok.
            if work.get('abstract'):
                metadata['AB'] = work['abstract']
            elif work.get('abstract_inverted_index'):
                ab = _decode_inverted_abstract(work['abstract_inverted_index'])
                if ab:
                    metadata['AB'] = ab

            # Concepts -> DE (anahtar kelime).
            if 'concepts' in work:
                concepts = [c['display_name'] for c in work['concepts'] if 'display_name' in c]
                if concepts:
                    metadata['DE'] = '; '.join(concepts)

            # Topics -> WC (subfield, daha özel) + SC (field, daha geniş). OpenAlex konu
            # sınıflandırması; WoS taksonomisiyle birebir değil ama açık/şeffaf. WC ve SC
            # ikisi de boş olan kayıtlarda devreye girer (first-non-empty-wins).
            topics = work.get('topics') or ([work['primary_topic']] if work.get('primary_topic') else [])
            if topics:
                subfields, fields = [], []
                for tp in topics:
                    if not isinstance(tp, dict):
                        continue
                    sf = (tp.get('subfield') or {}).get('display_name')
                    fd = (tp.get('field') or {}).get('display_name')
                    if sf:
                        subfields.append(sf)
                    if fd:
                        fields.append(fd)
                if subfields:
                    metadata['WC'] = '; '.join(dict.fromkeys(subfields))
                if fields:
                    metadata['SC'] = '; '.join(dict.fromkeys(fields))
            
            # Citations
            if 'cited_by_count' in work:
                metadata['TC'] = work['cited_by_count']
            
            # Open Access Information
            if 'open_access' in work:
                oa_info = work['open_access']
                oa_details = []
                if 'is_oa' in oa_info:
                    oa_details.append(f"is_oa: {oa_info['is_oa']}")
                if 'oa_status' in oa_info:
                    oa_details.append(f"status: {oa_info['oa_status']}")
                if 'oa_url' in oa_info:
                    oa_details.append(f"url: {oa_info['oa_url']}")
                if oa_details:
                    metadata['OA'] = '; '.join(oa_details)
            
            # Referenced Works — tümünü birleştir (Excel limiti için kısalt)
            if work.get('referenced_works'):
                metadata['CR'] = truncate_url_list('; '.join(work['referenced_works']))
            
            return metadata
    except Exception as e:
        print(f"OpenAlex API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_scopus(doi: str, api_key: str) -> dict:
    """Extract metadata from Scopus API (Requires API key)"""
    try:
        url = f"https://api.elsevier.com/content/abstract/doi/{doi}"
        headers = {
            'X-ELS-APIKey': api_key,
            'Accept': 'application/json'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'abstracts-retrieval-response' in data:
                work = data['abstracts-retrieval-response']
                metadata = {}
                
                # Coredata'yı kontrol et
                if 'coredata' in work:
                    coredata = work['coredata']
                    
                    # DOI
                    if 'prism:doi' in coredata:
                        metadata['DI'] = coredata['prism:doi']
                    elif 'dc:identifier' in coredata:
                        metadata['DI'] = coredata['dc:identifier']
                    
                    # Document Type
                    if 'prism:aggregationType' in coredata:
                        metadata['DT'] = coredata['prism:aggregationType']
                    
                    # Title
                    if 'dc:title' in coredata:
                        metadata['TI'] = coredata['dc:title']
                    
                    # Journal Name
                    if 'prism:publicationName' in coredata:
                        metadata['SO'] = coredata['prism:publicationName']
                    
                    # Publisher
                    if 'dc:publisher' in coredata:
                        metadata['PU'] = coredata['dc:publisher']
                    
                    # Volume
                    if 'prism:volume' in coredata:
                        metadata['VL'] = coredata['prism:volume']
                    
                    # Issue
                    if 'prism:issueIdentifier' in coredata:
                        metadata['IS'] = coredata['prism:issueIdentifier']
                    
                    # Page Numbers
                    if 'prism:startingPage' in coredata:
                        metadata['BP'] = coredata['prism:startingPage']
                    if 'prism:endingPage' in coredata:
                        metadata['EP'] = coredata['prism:endingPage']
                    
                    # Publication Year
                    if 'prism:coverDate' in coredata:
                        metadata['PY'] = coredata['prism:coverDate'][:4]
                    
                    # ISSN
                    if 'prism:issn' in coredata:
                        metadata['SN'] = coredata['prism:issn']
                    
                    # Abstract
                    if 'dc:description' in coredata:
                        metadata['AB'] = coredata['dc:description']
                    
                    # URLs
                    if 'link' in coredata:
                        urls = []
                        for link in coredata['link']:
                            if '@href' in link:
                                urls.append(link['@href'])
                        if urls:
                            metadata['UR'] = '; '.join(urls)
                    
                    # Citation Count
                    if 'citedby-count' in coredata:
                        metadata['TC'] = coredata['citedby-count']
                
                # Authors ve Affiliations
                if 'authors' in work:
                    authors = []
                    affiliations = []
                    author_affiliations = []
                    orcids = []  # WoS OI

                    if 'author' in work['authors']:
                        for author in work['authors']['author']:
                            author_name = []
                            if 'ce:given-name' in author:
                                author_name.append(author['ce:given-name'])
                            if 'ce:surname' in author:
                                author_name.append(author['ce:surname'])

                            if author_name:
                                full_name = ' '.join(author_name)
                                authors.append(full_name)
                                # ORCID (Scopus '@orcid')
                                oid = str(author.get('@orcid') or "").strip()
                                if oid:
                                    orcids.append(f"{full_name}/{oid}")

                                # Yazar affiliations
                                if 'affiliation' in author:
                                    author_affs = []
                                    if isinstance(author['affiliation'], list):
                                        for aff in author['affiliation']:
                                            if 'affilname' in aff:
                                                affiliations.append(aff['affilname'])
                                                author_affs.append(aff['affilname'])
                                    else:
                                        aff = author['affiliation']
                                        if 'affilname' in aff:
                                            affiliations.append(aff['affilname'])
                                            author_affs.append(aff['affilname'])
                                    
                                    if author_affs:
                                        author_affiliations.append(f"{full_name} [{'; '.join(author_affs)}]")
                    
                    if authors:
                        metadata['AU'] = '; '.join(authors)
                    if affiliations:
                        metadata['C1'] = '; '.join(list(dict.fromkeys(affiliations)))
                    if author_affiliations:
                        metadata['AF'] = '; '.join(author_affiliations)
                    if orcids:
                        metadata['OI'] = '; '.join(orcids)

                # Subject Areas (ASJC) -> DE + WC/SC. Yalnız Scopus key aktifse buraya gelinir.
                # NOT: tek alan olduğunda Elsevier 'subject-area'yı liste yerine TEK dict döndürür.
                sa = (work.get('subject-areas') or {}).get('subject-area')
                if isinstance(sa, dict):
                    sa = [sa]
                if isinstance(sa, list):
                    subjects = [s['$'] for s in sa if isinstance(s, dict) and '$' in s]
                    if subjects:
                        uniq = '; '.join(dict.fromkeys(subjects))
                        metadata['DE'] = uniq
                        metadata['WC'] = uniq
                        metadata['SC'] = uniq
                
                return metadata
    except Exception as e:
        print(f"Scopus API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_datacite(doi: str) -> dict:
    """Extract metadata from DataCite API (Free, no key required)"""
    try:
        url = f"https://api.datacite.org/dois/{doi}"
        headers = {
            'Accept': 'application/vnd.api+json'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                work = data['data']['attributes']
                
                # Extract relevant fields
                metadata = {}
                
                # Authors
                if 'creators' in work:
                    authors = []
                    for creator in work['creators']:
                        if isinstance(creator, dict):
                            name_parts = []
                            if 'givenName' in creator:
                                name_parts.append(creator['givenName'])
                            if 'familyName' in creator:
                                name_parts.append(creator['familyName'])
                            if name_parts:
                                authors.append(' '.join(name_parts))
                            elif 'name' in creator:
                                authors.append(creator['name'])
                    if authors:
                        metadata['AU'] = '; '.join(authors)
                
                # Title
                if 'titles' in work and work['titles']:
                    metadata['TI'] = work['titles'][0].get('title', '')
                
                # Publication Year
                if 'publicationYear' in work:
                    metadata['PY'] = work['publicationYear']
                
                # Document Type
                if 'types' in work and 'resourceTypeGeneral' in work['types']:
                    metadata['DT'] = work['types']['resourceTypeGeneral']
                
                # Keywords, Science Categories ve Web of Science Categories
                if 'subjects' in work and work['subjects']:
                    all_subjects = [subj.get('subject', '') for subj in work['subjects'] if 'subject' in subj]
                    if all_subjects:
                        metadata['DE'] = '; '.join(all_subjects)  # yalnız DE; WC/SC ML'den
                
                # Language
                if 'language' in work:
                    metadata['LA'] = work['language']
                
                # Publisher/Journal
                if 'publisher' in work:
                    metadata['SO'] = work['publisher']
                
                # Abstract
                if 'descriptions' in work:
                    abstracts = [desc.get('description', '') for desc in work['descriptions'] 
                               if desc.get('descriptionType', '').lower() == 'abstract']
                    if abstracts:
                        metadata['AB'] = abstracts[0]
                
                # Contributors (including affiliations)
                if 'contributors' in work:
                    affiliations = []
                    for contrib in work['contributors']:
                        if isinstance(contrib, dict) and 'affiliation' in contrib:
                            if isinstance(contrib['affiliation'], list):
                                affiliations.extend([aff.get('name', '') for aff in contrib['affiliation'] if 'name' in aff])
                            elif isinstance(contrib['affiliation'], str):
                                affiliations.append(contrib['affiliation'])
                    if affiliations:
                        metadata['C1'] = '; '.join(set(affiliations))  # Remove duplicates
                
                return metadata
    except Exception as e:
        print(f"DataCite API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_unpaywall(doi: str, email: str) -> dict:
    """Extract metadata from Unpaywall API (Free, requires email)"""
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        response = _get_with_retry(url)
        
        if response.status_code == 200:
            work = response.json()
            metadata = {}
            
            # Authors
            if 'z_authors' in work:
                authors = [f"{a.get('given', '')} {a.get('family', '')}" for a in work['z_authors']]
                metadata['AU'] = '; '.join(authors)
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            
            # Journal
            if 'journal_name' in work:
                metadata['SO'] = work['journal_name']
            
            # Publication Year
            if 'published_date' in work:
                try:
                    metadata['PY'] = work['published_date'][:4]
                except:
                    pass
            
            # Document Type
            if 'genre' in work:
                metadata['DT'] = work['genre']
            
            return metadata
    except Exception as e:
        print(f"Unpaywall API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_europepmc(doi: str) -> dict:
    """Extract metadata from Europe PMC API (Free)"""
    try:
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json"
        response = _get_with_retry(url)
        
        if response.status_code == 200:
            data = response.json()
            if 'resultList' in data and 'result' in data['resultList'] and data['resultList']['result']:
                work = data['resultList']['result'][0]
                metadata = {}
                
                # DOI
                if 'doi' in work:
                    metadata['DI'] = work['doi']
                
                # Document Type
                if 'pubType' in work:
                    metadata['DT'] = work['pubType']
                
                # Title
                if 'title' in work:
                    metadata['TI'] = work['title']
                
                # Authors
                if 'authorString' in work:
                    metadata['AU'] = work['authorString']
                
                # Journal Title
                if 'journalTitle' in work:
                    metadata['SO'] = work['journalTitle']
                
                # Volume
                if 'journalVolume' in work:
                    metadata['VL'] = work['journalVolume']
                
                # Issue
                if 'journalIssue' in work:
                    metadata['IS'] = work['journalIssue']
                
                # Page Information
                if 'pageInfo' in work:
                    pages = work['pageInfo'].split('-')
                    if len(pages) == 2:
                        metadata['BP'] = pages[0].strip()  # Beginning Page
                        metadata['EP'] = pages[1].strip()  # Ending Page
                
                # Publication Year
                if 'pubYear' in work:
                    metadata['PY'] = work['pubYear']
                
                # ISSN
                if 'journalIssn' in work:
                    metadata['SN'] = work['journalIssn']
                
                # Abstract
                if 'abstractText' in work:
                    metadata['AB'] = work['abstractText']
                
                # URLs
                urls = []
                if 'sourceUrl' in work:
                    urls.append(work['sourceUrl'])
                if 'fullTextUrlList' in work and 'fullTextUrl' in work['fullTextUrlList']:
                    for url_info in work['fullTextUrlList']['fullTextUrl']:
                        if 'url' in url_info:
                            urls.append(url_info['url'])
                if urls:
                    metadata['UR'] = '; '.join(urls)
                
                # Citations
                if 'citedByCount' in work:
                    metadata['TC'] = work['citedByCount']
                
                # Open Access Information
                if 'isOpenAccess' in work:
                    oa_info = []
                    oa_info.append(f"is_oa: {work['isOpenAccess']}")
                    if 'fullTextUrlList' in work and 'fullTextUrl' in work['fullTextUrlList']:
                        for url_info in work['fullTextUrlList']['fullTextUrl']:
                            if 'availability' in url_info:
                                oa_info.append(f"availability: {url_info['availability']}")
                            if 'availabilityCode' in url_info:
                                oa_info.append(f"status: {url_info['availabilityCode']}")
                    if oa_info:
                        metadata['OA'] = '; '.join(oa_info)
                
                # Additional Information
                additional_info = []
                if 'inEPMC' in work:
                    additional_info.append(f"in_epmc: {work['inEPMC']}")
                if 'inPMC' in work:
                    additional_info.append(f"in_pmc: {work['inPMC']}")
                if 'hasPDF' in work:
                    additional_info.append(f"has_pdf: {work['hasPDF']}")
                if 'hasReferences' in work:
                    additional_info.append(f"has_references: {work['hasReferences']}")
                if 'hasTextMinedTerms' in work:
                    additional_info.append(f"has_text_mined_terms: {work['hasTextMinedTerms']}")
                if 'hasDbCrossReferences' in work:
                    additional_info.append(f"has_db_cross_references: {work['hasDbCrossReferences']}")
                if additional_info:
                    metadata['AI'] = '; '.join(additional_info)
                
                return metadata
    except Exception as e:
        print(f"Europe PMC API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_semantic_scholar(doi: str, api_key: str = None) -> dict:
    """Extract metadata from Semantic Scholar API (Free, API key optional but recommended)"""
    try:
        url = f"https://api.semanticscholar.org/v1/paper/{doi}"
        headers = {'x-api-key': api_key} if api_key else {}
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            work = response.json()
            metadata = {}
            
            # Paper ID
            if 'paperId' in work:
                metadata['ID'] = work['paperId']
            
            # DOI
            if 'externalIds' in work and 'DOI' in work['externalIds']:
                metadata['DI'] = work['externalIds']['DOI']
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            
            # Authors
            if 'authors' in work:
                authors = []
                for author in work['authors']:
                    if 'name' in author:
                        authors.append(author['name'])
                
                if authors:
                    metadata['AU'] = '; '.join(authors)
            
            # Abstract
            if 'abstract' in work:
                metadata['AB'] = work['abstract']
            
            # Publication Year
            if 'year' in work:
                metadata['PY'] = work['year']
            
            # Citations
            if 'citationCount' in work:
                metadata['TC'] = work['citationCount']
            
            # Reference Count
            if 'referenceCount' in work:
                metadata['RC'] = work['referenceCount']
            
            # URL
            if 'url' in work:
                metadata['UR'] = work['url']
            
            # Fields of Study -> yalnız DE; WC/SC ML'den
            if 'fieldsOfStudy' in work and work['fieldsOfStudy']:
                metadata['DE'] = '; '.join(work['fieldsOfStudy'])
            
            # Additional Identifiers
            if 'externalIds' in work:
                external_ids = []
                for id_type, id_value in work['externalIds'].items():
                    if id_type != 'DOI':  # DOI already handled
                        external_ids.append(f"{id_type}: {id_value}")
                if external_ids:
                    metadata['EI'] = '; '.join(external_ids)
            
            return metadata
    except Exception as e:
        print(f"Semantic Scholar API Error: {str(e)}")
    return {}

def truncate_url_list(url_list: str, max_length: int = 2079) -> str:
    """Excel'in URL uzunluk sınırına uygun şekilde URL listesini kısalt"""
    # Eğer URL listesi boşsa veya zaten sınırın altındaysa direkt döndür
    if not url_list or len(url_list) <= max_length:
        return url_list
    
    # URL'leri ayır
    urls = url_list.split('; ')
    result = []
    current_length = 0
    
    # Her URL için
    for i, url in enumerate(urls):
        # İlk URL için ayırıcı gerekmez, diğerleri için '; ' (2 karakter) gerekir
        separator_length = 0 if not result else 2
        
        # Bu URL'yi eklersek toplam uzunluk
        new_length = current_length + len(url) + separator_length
        
        # Eğer bu URL'yi eklemek sınırı aşmayacaksa
        if new_length <= max_length - 3:  # "..." için 3 karakter rezerve et
            result.append(url)
            current_length = new_length
        else:
            # Sınıra ulaşıldı, "..." ekle ve döngüyü kır
            result.append("...")
            break
    
    # URL'leri birleştir
    return '; '.join(result)

def extract_metadata(doi: str, current_data: dict, scopus_api_key: str = None, semantic_scholar_key: str = None, unpaywall_email: str = None, crossref_email: str = None) -> dict:
    """Try to extract metadata from multiple sources"""
    metadata = current_data.copy()
    api_sources = {}  # Track which fields came from which API

    # Erken çıkış için: API'lerin doldurabileceği alanlar. OI(ORCID)/RI/ROR/CC yeni
    # eklendi (yazar/kurum kimliği — disambiguation için).
    _FILLABLE = ("DI", "DT", "AU", "AF", "TI", "PY", "SO", "PU", "SN", "UR", "AB", "DE",
                 "C1", "TC", "CR", "LA", "WC", "SC", "OI", "RI", "ROR", "CC")

    def _is_blank(v) -> bool:
        if v is None:
            return True
        try:
            if pd.isna(v):
                return True
        except (TypeError, ValueError):
            pass
        return str(v).strip() in ("", "nan", "NaN", "None")

    def _remaining() -> bool:
        return any(_is_blank(metadata.get(f)) for f in _FILLABLE)

    try:
        # Get API credentials from config file
        if not scopus_api_key:
            scopus_api_key = get_api_credential('scopus', 'api_key')
        if not semantic_scholar_key:
            semantic_scholar_key = get_api_credential('semantic_scholar', 'api_key')
        if not unpaywall_email:
            unpaywall_email = get_api_credential('unpaywall', 'email')
        if not crossref_email:
            crossref_email = get_api_credential('crossref', 'email')

        if not scopus_api_key:
            print("\nWarning: Scopus API key not found in API_config.json")
            print("You can add it to enable Scopus metadata enrichment.")
            print("Continuing with other data sources...")

        if not unpaywall_email:
            print("\nWarning: Unpaywall email not found in API_config.json")
            print("You can add it to enable Unpaywall metadata enrichment.")
            print("Continuing with other data sources...")
        
        # CrossRef
        print(f"\nTrying CrossRef API...", end='')
        try:
            crossref_data = extract_metadata_from_crossref(doi, crossref_email)
            if crossref_data:
                for key, value in crossref_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        # URL içeren alanları kısalt
                        if isinstance(value, str) and ('http://' in value or 'https://' in value):
                            value = truncate_url_list(value)
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'CrossRef'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # OpenAlex
        print(f"Trying OpenAlex API...", end='')
        try:
            openalex_data = extract_metadata_from_openalex(doi, crossref_email)
            if openalex_data:
                for key, value in openalex_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        # URL içeren alanları kısalt
                        if isinstance(value, str) and ('http://' in value or 'https://' in value):
                            value = truncate_url_list(value)
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'OpenAlex'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")

        # Erken çıkış: CrossRef + OpenAlex sonrası doldurulacak alan kalmadıysa
        # diğer 5 API'yi hiç deneme (en büyük hız kazancı).
        if not _remaining():
            for field in metadata:
                if isinstance(metadata[field], str) and ('http://' in metadata[field] or 'https://' in metadata[field]):
                    metadata[field] = truncate_url_list(metadata[field])
            if api_sources:
                metadata['API_Sources'] = api_sources
            return metadata

        # Scopus
        if scopus_api_key:
            print(f"Trying Scopus API...", end='')
            try:
                scopus_data = extract_metadata_from_scopus(doi, scopus_api_key)
                if scopus_data:
                    for key, value in scopus_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Scopus'
                    print(" [SUCCESS]")
                else:
                    response = requests.get(f"https://api.elsevier.com/content/abstract/doi/{doi}",
                                         headers={'X-ELS-APIKey': scopus_api_key, 'Accept': 'application/json'}, timeout=REQUEST_TIMEOUT)
                    print(f" [NO DATA - Status: {response.status_code}, Response: {response.text[:100]}...]")
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
        
        # DataCite
        print(f"Trying DataCite API...", end='')
        try:
            response = requests.get(f"https://api.datacite.org/dois/{doi}", headers={'Accept': 'application/vnd.api+json'}, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                print(f" [NO DATA - Status: {response.status_code}]")
            else:
                datacite_data = extract_metadata_from_datacite(doi)
                if datacite_data:
                    for key, value in datacite_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'DataCite'
                    print(" [SUCCESS]")
                else:
                    print(f" [NO DATA - Empty Response]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # Unpaywall
        if unpaywall_email:
            print(f"Trying Unpaywall API...", end='')
            try:
                unpaywall_data = extract_metadata_from_unpaywall(doi, unpaywall_email)
                if unpaywall_data:
                    for key, value in unpaywall_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Unpaywall'
                    print(" [SUCCESS]")
                else:
                    print(" [NO DATA]")
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
        
        # Europe PMC
        print(f"Trying Europe PMC API...", end='')
        try:
            response = requests.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json", timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                print(f" [NO DATA - Status: {response.status_code}]")
            else:
                europepmc_data = extract_metadata_from_europepmc(doi)
                if europepmc_data:
                    for key, value in europepmc_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Europe PMC'
                    print(" [SUCCESS]")
                else:
                    data = response.json()
                    hit_count = data.get('hitCount', 0)
                    print(f" [NO DATA - Hit Count: {hit_count}]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # Semantic Scholar
        print(f"Trying Semantic Scholar API...", end='')
        try:
            semantic_data = extract_metadata_from_semantic_scholar(doi, semantic_scholar_key)
            if semantic_data:
                for key, value in semantic_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'Semantic Scholar'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # API kaynaklarını ekle
        if api_sources:
            metadata['API_Sources'] = api_sources
        
        # Son kontrol: Tüm URL içeren alanları kontrol et ve gerekirse kısalt
        for field in metadata:
            if isinstance(metadata[field], str) and ('http://' in metadata[field] or 'https://' in metadata[field]):
                metadata[field] = truncate_url_list(metadata[field])
        
        return metadata
        
    except Exception as e:
        print(f"Error processing DOI {doi}: {str(e)}")
        return current_data 