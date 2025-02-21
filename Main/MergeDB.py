import pandas as pd
import re
import os
from typing import List, Union
import numpy as np
from unidecode import unidecode

def trim(text: str) -> str:
    """Removes extra spaces from text"""
    if pd.isna(text):
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def merge_values(x):
    """
    Merges values from a pandas Series, handling NaN values and duplicates.
    Used for combining values during database merging.
    """
    if x.empty:
        return ""
    # Get first non-NaN value
    values = [str(val) for val in x if pd.notna(val)]
    if not values:
        return ""
    return values[0]

def meta_tag_extraction(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    """Creates SR (Source) tag"""
    if 'AU' in df.columns and 'PY' in df.columns:
        df['SR'] = df.apply(lambda row: f"{row['AU'].split(';')[0]} {row['PY']}", axis=1)
    return df

def clean_merged_values(x: str) -> str:
    """Clean merged values by removing extra semicolons, spaces and duplicates"""
    if not isinstance(x, str):
        return x
    
    # Split by semicolon and clean each part
    parts = [part.strip() for part in x.split(';')]
    
    # Remove empty parts and duplicates while preserving order
    seen = set()
    cleaned_parts = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            cleaned_parts.append(part)
    
    # Join back with semicolon
    return '; '.join(cleaned_parts)

def merge_author_fields(wos_authors: str, scopus_authors: str) -> str:
    """
    Merges author fields using WoS format as reference
    
    Args:
        wos_authors (str): Author list from WoS
        scopus_authors (str): Author list from Scopus
    
    Returns:
        str: Merged author list in WoS format
    """
    def normalize_author(author):
        # Clean spaces
        author = re.sub(r'\s+', ' ', author.strip())
        # Normalize special characters
        author = unidecode(author)
        # Convert to uppercase
        return author.upper()
    
    def get_author_key(author):
        # Create key for author matching
        parts = normalize_author(author).split()
        if not parts:
            return ''
        return re.sub(r'[^A-Z]', '', parts[0])
    
    # Process WoS authors
    wos_authors = [a.strip() for a in wos_authors.split(';') if a.strip()]
    wos_dict = {get_author_key(author): author for author in wos_authors}
    
    # Process Scopus authors
    scopus_authors = [a.strip() for a in scopus_authors.split(';') if a.strip()]
    
    # Result list (add WoS authors first)
    merged_authors = wos_authors.copy()
    
    # Add missing authors from Scopus
    for scopus_author in scopus_authors:
        author_key = get_author_key(scopus_author)
        # Add if author not in WoS
        if author_key not in wos_dict:
            merged_authors.append(scopus_author)
    
    return '; '.join(merged_authors)

def merge_author_fullnames(wos_af: str, scopus_af: str) -> str:
    """
    Merges author full names using WoS format as reference
    
    Args:
        wos_af (str): Author full names from WoS
        scopus_af (str): Author full names from Scopus
    
    Returns:
        str: Merged author full names in WoS format
    """
    def clean_author_name(author):
        # Remove IDs in parentheses
        author = re.sub(r'\s*\([^)]*\)', '', author)
        # Clean spaces
        author = re.sub(r'\s+', ' ', author.strip())
        # Normalize special characters
        author = unidecode(author)
        return author
    
    def get_author_key(author):
        # Create key for author matching (LASTNAME, FIRSTNAME)
        parts = clean_author_name(author).split(',', 1)
        if len(parts) < 2:
            return ''
        lastname = parts[0].strip()
        return lastname.upper()
    
    # Process WoS authors
    wos_authors = [a.strip() for a in wos_af.split(';') if a.strip()]
    wos_dict = {get_author_key(author): author for author in wos_authors}
    
    # Result list (start with WoS authors)
    merged_authors = wos_authors.copy()
    
    # Process Scopus authors
    if scopus_af:
        scopus_authors = [a.strip() for a in scopus_af.split(';') if a.strip()]
        
        # Add missing authors from Scopus
        for scopus_author in scopus_authors:
            author_key = get_author_key(scopus_author)
            # Add if author not in WoS
            if author_key and author_key not in wos_dict:
                clean_author = clean_author_name(scopus_author)
                merged_authors.append(clean_author)
    
    return ';'.join(merged_authors)

def merge_address_fields(wos_addresses: str, scopus_addresses: str) -> str:
    """
    Merges C1 (Author Addresses) field.
    Priority order:
    1. Uses Scopus address if available
    2. Uses WoS address if Scopus is empty
    3. Returns empty string if both are empty
    
    Args:
        wos_addresses (str): Address information from WoS
        scopus_addresses (str): Address information from Scopus
    
    Returns:
        str: Selected address information
    """
    try:
        # Clean and check Scopus addresses
        if pd.notna(scopus_addresses) and str(scopus_addresses).strip():
            return str(scopus_addresses).strip()
            
        # Clean and check WoS addresses
        if pd.notna(wos_addresses) and str(wos_addresses).strip():
            return str(wos_addresses).strip()
            
        return ''
        
    except Exception:
        return ''

def merge_reprint_author(wos_rp: str, scopus_rp: str) -> str:
    """
    Merges reprint author information from WoS and Scopus.
    Takes WoS data if available, otherwise takes Scopus data.
    No formatting is applied.
    
    Args:
        wos_rp (str): Reprint author from WoS
        scopus_rp (str): Reprint author from Scopus
    
    Returns:
        str: Original reprint author information from either source
    """
    # If WoS data exists, use it
    if pd.notna(wos_rp) and str(wos_rp).strip():
        return str(wos_rp).strip()
    
    # If only Scopus data exists, use it
    if pd.notna(scopus_rp) and str(scopus_rp).strip():
        return str(scopus_rp).strip()
    
    # If neither exists, return empty string
    return ''

def merge_references(wos_refs: str, scopus_refs: str) -> str:
    """
    WoS ve Scopus referanslarını birleştirir.
    
    Args:
        wos_refs (str): WoS'tan gelen referanslar (noktalı virgülle ayrılmış)
        scopus_refs (str): Scopus'tan gelen referanslar (noktalı virgülle ayrılmış)
    
    Returns:
        str: Birleştirilmiş ve temizlenmiş referanslar
    """
    def split_and_clean_refs(refs_str):
        if pd.isna(refs_str) or not refs_str:
            return []
        return [ref.strip() for ref in refs_str.split(';') if ref.strip()]
    
    def clean_text(text):
        # Tüm özel karakterleri kaldır (nokta, boşluk, virgül vb.)
        # Sadece harf ve rakamları tut
        return re.sub(r'[^A-Z0-9]', '', text.upper())
    
    def create_ref_key(ref):
        ref = ref.upper().strip()
        
        # Scopus formatı için (sonda yıl parantez içinde)
        if ref.endswith(')'):
            year_match = re.search(r'\((\d{4})\)$', ref)
            if year_match:
                year = year_match.group(1)
                # İlk virgüle kadar olan kısmı yazar olarak al ve temizle
                author = clean_text(ref.split(',')[0])
                return f"{author}_{year}"
        
        # WoS formatı için
        parts = ref.split(',')
        if len(parts) >= 2:
            author = clean_text(parts[0])
            year = parts[1].strip()
            # Yıl içindeki sayıları al
            year_match = re.search(r'\d{4}', year)
            if year_match:
                year = year_match.group()
                return f"{author}_{year}"
        
        # Eğer format tanınmazsa, tüm metni temizle
        return clean_text(ref)
    
    # Referansları listelere ayır
    wos_list = split_and_clean_refs(wos_refs)
    scopus_list = split_and_clean_refs(scopus_refs)
    
    # Her referans için anahtar oluştur
    wos_dict = {create_ref_key(ref): ref for ref in wos_list}
    scopus_dict = {create_ref_key(ref): ref for ref in scopus_list}
    
    # Tüm benzersiz anahtarları al
    all_keys = set(wos_dict.keys()) | set(scopus_dict.keys())
    
    # Birleştirilmiş referansları oluştur
    merged_refs = []
    for key in all_keys:
        # WoS formatını tercih et
        if key in wos_dict:
            merged_refs.append(wos_dict[key])
        else:
            merged_refs.append(scopus_dict[key])
    
    # Referansları birleştir
    return '; '.join(merged_refs)

def merge_abstracts(wos_ab: str, scopus_ab: str) -> str:
    """
    Merges abstract information from WoS and Scopus
    
    Args:
        wos_ab (str): Abstract from WoS
        scopus_ab (str): Abstract from Scopus
    
    Returns:
        str: Merged abstract in enhanced format
    """
    def clean_abstract(ab):
        if pd.isna(ab) or not ab:
            return ""
        # Temizleme işlemleri
        ab = re.sub(r'\s+', ' ', ab.strip())
        # Copyright bilgisini kaldır
        ab = re.sub(r'©.*?RESERVED\.?$', '', ab, flags=re.IGNORECASE)
        return ab.strip()
    
    # Her iki kaynaktan gelen abstract'leri temizle
    wos_ab = clean_abstract(wos_ab)
    scopus_ab = clean_abstract(scopus_ab)
    
    # Eğer sadece bir kaynak varsa, onu kullan
    if not wos_ab:
        return scopus_ab
    if not scopus_ab:
        return wos_ab
    
    # Her iki kaynak da varsa, daha uzun olanı tercih et
    return wos_ab if len(wos_ab) > len(scopus_ab) else scopus_ab

def merge_keywords(wos_keywords: str, scopus_keywords: str) -> str:
    """
    Merges author keywords from WoS and Scopus while normalizing special letters.
    Preserves special characters and case, only normalizes language-specific letters.
    
    Args:
        wos_keywords (str): Keywords from WoS
        scopus_keywords (str): Keywords from Scopus
    
    Returns:
        str: Merged keywords with duplicates removed
    """
    def clean_keyword(kw):
        if pd.isna(kw) or not kw:
            return ""
        # Remove extra spaces
        kw = re.sub(r'\s+', ' ', kw.strip())
        # Normalize special letters (é->e, ñ->n, etc.) while preserving case
        kw = unidecode(kw)
        return kw
    
    # Split and clean keywords
    wos_kws = [clean_keyword(kw) for kw in str(wos_keywords).split(';') if clean_keyword(kw)]
    scopus_kws = [clean_keyword(kw) for kw in str(scopus_keywords).split(';') if clean_keyword(kw)]
    
    # Create a case-insensitive set for duplicate checking
    seen = set()
    unique_keywords = []
    
    # Process all keywords
    for kw in wos_kws + scopus_kws:
        # Use uppercase version for checking duplicates
        kw_upper = kw.upper()
        if kw_upper not in seen:
            seen.add(kw_upper)
            unique_keywords.append(kw)
    
    # Sort alphabetically (case-insensitive) for consistency
    unique_keywords.sort(key=str.upper)
    
    return '; '.join(unique_keywords)

def merge_index_keywords(wos_keywords: str, scopus_keywords: str) -> str:
    """
    Merges index keywords from WoS and Scopus while normalizing special letters.
    Preserves special characters and case, only normalizes language-specific letters.
    
    Args:
        wos_keywords (str): Keywords from WoS (Keywords Plus)
        scopus_keywords (str): Keywords from Scopus (Index Keywords)
    
    Returns:
        str: Merged keywords with duplicates removed
    """
    def clean_keyword(kw):
        if pd.isna(kw) or not kw:
            return ""
        # Remove extra spaces
        kw = re.sub(r'\s+', ' ', kw.strip())
        # Normalize special letters (é->e, ñ->n, etc.) while preserving case
        kw = unidecode(kw)
        return kw
    
    # Split and clean keywords
    wos_kws = [clean_keyword(kw) for kw in str(wos_keywords).split(';') if clean_keyword(kw)]
    scopus_kws = [clean_keyword(kw) for kw in str(scopus_keywords).split(';') if clean_keyword(kw)]
    
    # Create a case-insensitive set for duplicate checking
    seen = set()
    unique_keywords = []
    
    # Process all keywords
    for kw in wos_kws + scopus_kws:
        # Use uppercase version for checking duplicates
        kw_upper = kw.upper()
        if kw_upper not in seen:
            seen.add(kw_upper)
            unique_keywords.append(kw)
    
    # Sort alphabetically (case-insensitive) for consistency
    unique_keywords.sort(key=str.upper)
    
    return '; '.join(unique_keywords)

def merge_publisher(wos_pub: str, scopus_pub: str) -> str:
    """
    Merges publisher information from WoS and Scopus, preferring the longer/full name.
    Cleans and standardizes publisher names.
    
    Args:
        wos_pub (str): Publisher from WoS
        scopus_pub (str): Publisher from Scopus
    
    Returns:
        str: Merged publisher name in standardized format
    """
    def clean_publisher(pub):
        if pd.isna(pub) or not pub:
            return ""
        # Remove extra spaces
        pub = re.sub(r'\s+', ' ', pub.strip())
        # Normalize special characters while preserving case
        pub = unidecode(pub)
        return pub
    
    # Clean both publisher names
    wos_pub = clean_publisher(wos_pub)
    scopus_pub = clean_publisher(scopus_pub)
    
    # If only one source has data, use that
    if not wos_pub:
        return scopus_pub
    if not scopus_pub:
        return wos_pub
    
    # Compare lengths and use the longer name (usually more complete)
    if len(scopus_pub) > len(wos_pub):
        return scopus_pub
    return wos_pub

def merge_language(wos_lang: str, scopus_lang: str) -> str:
    """
    Merges language information from WoS and Scopus.
    Standardizes language names and handles multiple languages.
    If no language data is available, defaults to "ENGLISH".
    
    Args:
        wos_lang (str): Language from WoS
        scopus_lang (str): Language from Scopus
    
    Returns:
        str: Standardized language name(s)
    """
    # Language code/name mapping
    LANGUAGE_MAP = {
        # Common codes
        'ENG': 'ENGLISH',
        'EN': 'ENGLISH',
        'FRE': 'FRENCH',
        'FR': 'FRENCH',
        'GER': 'GERMAN',
        'DE': 'GERMAN',
        'SPA': 'SPANISH',
        'ES': 'SPANISH',
        'ITA': 'ITALIAN',
        'IT': 'ITALIAN',
        'POR': 'PORTUGUESE',
        'PT': 'PORTUGUESE',
        'RUS': 'RUSSIAN',
        'RU': 'RUSSIAN',
        'CHI': 'CHINESE',
        'ZH': 'CHINESE',
        'JPN': 'JAPANESE',
        'JA': 'JAPANESE',
        # Full names with special characters
        'ESPANOL': 'SPANISH',
        'ESPAÑOL': 'SPANISH',
        'FRANCAIS': 'FRENCH',
        'FRANÇAIS': 'FRENCH',
        'DEUTSCHE': 'GERMAN',
        'PORTUGUES': 'PORTUGUESE',
        'PORTUGUÊS': 'PORTUGUESE',
        'ITALIANO': 'ITALIAN',
        'RUSSKIY': 'RUSSIAN',
        'РУССКИЙ': 'RUSSIAN',
        '中文': 'CHINESE',
        '日本語': 'JAPANESE'
    }
    
    def standardize_language(lang):
        if pd.isna(lang) or not lang:
            return ""
        # Clean and normalize
        lang = re.sub(r'\s+', ' ', lang.strip())
        lang = unidecode(lang).upper()
        
        # Split if multiple languages
        languages = [l.strip() for l in lang.split(';')]
        
        # Standardize each language
        standardized = []
        for l in languages:
            # Check if it's in our mapping
            if l in LANGUAGE_MAP:
                standardized.append(LANGUAGE_MAP[l])
            else:
                standardized.append(l)
        
        return '; '.join(sorted(set(standardized)))
    
    # Clean and standardize both inputs
    wos_lang = standardize_language(wos_lang)
    scopus_lang = standardize_language(scopus_lang)
    
    # If both sources have data
    if wos_lang and scopus_lang:
        # Combine languages from both sources
        all_langs = set(wos_lang.split('; ') + scopus_lang.split('; '))
        return '; '.join(sorted(all_langs))
    
    # If only one source has data
    if wos_lang:
        return wos_lang
    if scopus_lang:
        return scopus_lang
    
    # If no language data is available, default to ENGLISH
    return "ENGLISH"

def merge_document_type(wos_dt: str, scopus_dt: str) -> str:
    """
    Merges document type information from WoS and Scopus.
    Combines document types from both sources with semicolons.
    Case-insensitive comparison to avoid duplicates.
    
    Args:
        wos_dt (str): Document type from WoS
        scopus_dt (str): Document type from Scopus
    
    Returns:
        str: Combined document types from both sources
    """
    def clean_doctype(dt):
        if pd.isna(dt) or not dt:
            return ""
        # Remove extra spaces and convert to uppercase
        dt = re.sub(r'\s+', ' ', str(dt).strip()).upper()
        # Normalize special characters
        dt = unidecode(dt)
        # Remove any remaining special characters
        dt = re.sub(r'[^A-Z0-9\s]', '', dt)
        return dt
    
    # Clean both inputs
    wos_dt = clean_doctype(wos_dt)
    scopus_dt = clean_doctype(scopus_dt)
    
    # If both are empty, return empty string
    if not wos_dt and not scopus_dt:
        return ""
    
    # If only one source has data, return that
    if not wos_dt:
        return scopus_dt
    if not scopus_dt:
        return wos_dt
    
    # Split multiple document types if they exist
    wos_types = set(dt.strip() for dt in wos_dt.split(';') if dt.strip())
    scopus_types = set(dt.strip() for dt in scopus_dt.split(';') if dt.strip())
    
    # Combine unique document types
    all_types = wos_types | scopus_types
    
    # Sort for consistency
    return '; '.join(sorted(all_types))


def merge_url(wos_url: str, scopus_url: str) -> str:
    """
    Merges URL information from WoS and Scopus.
    Prioritizes WoS URL if available, otherwise uses Scopus URL.
    
    Args:
        wos_url (str): URL from WoS
        scopus_url (str): URL from Scopus (Link)
    
    Returns:
        str: URL, preferring WoS format when available
    """
    def clean_url(url):
        if pd.isna(url) or not url:
            return ""
        return str(url).strip()
    
    # Clean both URLs
    wos_url = clean_url(wos_url)
    scopus_url = clean_url(scopus_url)
    
    # Return WoS URL if available, otherwise Scopus URL
    return wos_url if wos_url else scopus_url

def merge_open_access(wos_oa: str, scopus_oa: str) -> str:
    """
    Merges Open Access information from WoS and Scopus.
    Standardizes OA status and combines information from both sources.
    
    Args:
        wos_oa (str): Open Access status from WoS
        scopus_oa (str): Open Access status from Scopus
    
    Returns:
        str: Standardized Open Access status
    """
    # OA status mapping dictionary
    OA_STATUS_MAP = {
        # Common variations
        'OPEN ACCESS': 'OPEN ACCESS',
        'OA': 'OPEN ACCESS',
        'GOLD': 'GOLD OPEN ACCESS',
        'GOLD OPEN ACCESS': 'GOLD OPEN ACCESS',
        'GREEN': 'GREEN OPEN ACCESS',
        'GREEN OPEN ACCESS': 'GREEN OPEN ACCESS',
        'BRONZE': 'BRONZE OPEN ACCESS',
        'BRONZE OPEN ACCESS': 'BRONZE OPEN ACCESS',
        'HYBRID': 'HYBRID OPEN ACCESS',
        'HYBRID OPEN ACCESS': 'HYBRID OPEN ACCESS',
        # Additional variations
        'ALL OPEN ACCESS': 'OPEN ACCESS',
        'PUBLISHED': 'OPEN ACCESS',
        'FREE': 'OPEN ACCESS',
        'PUBLISHERFULLGOLD': 'GOLD OPEN ACCESS',
        'REPOSITORY': 'GREEN OPEN ACCESS',
        # Non-OA variations
        'SUBSCRIPTION': 'NON OPEN ACCESS',
        'NON-OA': 'NON OPEN ACCESS',
        'CLOSED': 'NON OPEN ACCESS'
    }
    
    def standardize_oa_status(oa):
        if pd.isna(oa) or not oa:
            return ""
        # Remove extra spaces and convert to uppercase
        oa = re.sub(r'\s+', ' ', str(oa).strip()).upper()
        # Normalize special characters
        oa = unidecode(oa)
        # Map to standard status if exists
        return OA_STATUS_MAP.get(oa, oa)
    
    # Clean and standardize both inputs
    wos_oa = standardize_oa_status(wos_oa)
    scopus_oa = standardize_oa_status(scopus_oa)
    
    # If only one source has data, use that
    if not wos_oa and scopus_oa:
        return scopus_oa
    if wos_oa and not scopus_oa:
        return wos_oa
    
    # If both sources have data and they're different
    if wos_oa and scopus_oa and wos_oa != scopus_oa:
        # Prefer more specific OA type over general "OPEN ACCESS"
        if wos_oa == 'OPEN ACCESS':
            return scopus_oa
        if scopus_oa == 'OPEN ACCESS':
            return wos_oa
        # If both have specific types, prefer WoS
        return wos_oa
    
    # If both are the same or empty
    return wos_oa or 'NON OPEN ACCESS'

def clean_scopus_author_fullnames(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans Scopus author full names by removing IDs and converting to WoS format.
    Example input: "CAO, NANNAN (58490132900)"
    Example output: "CAO, NANNAN"
    
    Args:
        df (pd.DataFrame): DataFrame containing Scopus data
        
    Returns:
        pd.DataFrame: DataFrame with cleaned author full names
    """
    if 'AF' not in df.columns:
        return df
        
    def clean_author(author_str):
        if pd.isna(author_str) or not author_str:
            return ""
            
        # Split multiple authors
        authors = [a.strip() for a in author_str.split(';')]
        cleaned_authors = []
        
        for author in authors:
            # Remove ID in parentheses
            author = re.sub(r'\s*\([^)]*\)', '', author)
            # Clean extra spaces
            author = re.sub(r'\s+', ' ', author.strip())
            if author:
                cleaned_authors.append(author)
                
        return '; '.join(cleaned_authors)
    
    df['AF'] = df['AF'].apply(clean_author)
    return df

def merge_source_title(wos_so: str, scopus_so: str) -> str:
    """
    Merges source title information from WoS and Scopus.
    When both sources have data, prefers Scopus.
    When only one source has data, uses that source.
    
    Args:
        wos_so (str): Source title from WoS
        scopus_so (str): Source title from Scopus
    
    Returns:
        str: Source title, preferring Scopus when both exist
    """
    def clean_title(title):
        if pd.isna(title) or not title:
            return ""
        # Remove extra spaces
        title = re.sub(r'\s+', ' ', str(title).strip())
        return title
    
    # Clean both titles
    wos_so = clean_title(wos_so)
    scopus_so = clean_title(scopus_so)
    
    # If Scopus has data, use it
    if scopus_so:
        return scopus_so
    # Otherwise use WoS if it has data
    if wos_so:
        return wos_so
    # If neither has data, return empty string
    return ""

def merge_db_sources(*dataframes: pd.DataFrame, remove_duplicated: bool = True, merge_fields: bool = True, verbose: bool = False) -> pd.DataFrame:
    """
    Merges bibliometric data from different databases.
    Combines information from different columns of the same records to create the most complete data.
    
    Parameters:
    -----------
    *dataframes : pd.DataFrame
        Bibliographic data frames to be merged
    remove_duplicated : bool, default=True
        If True, duplicate documents are removed
    merge_fields : bool, default=True
        If True, information from different columns of the same records is merged
    verbose : bool, default=False
        If True, prints information about duplicate documents
    
    Returns:
    --------
    pd.DataFrame
        Merged bibliographic data frame
    """
    
    if not dataframes:
        raise ValueError("At least one data frame is required!")
    
    # Clean Scopus author full names before merging
    cleaned_dataframes = []
    for df in dataframes:
        if 'DB' in df.columns:
            # Create temporary RP columns based on source
            if df['DB'].iloc[0] == 'SCOPUS' and 'RP' in df.columns:
                df['RP_SCOPUS'] = df['RP']
                df['RP_WOS'] = ''
            elif df['DB'].iloc[0] == 'ISI' and 'RP' in df.columns:
                df['RP_WOS'] = df['RP']
                df['RP_SCOPUS'] = ''
            
        if 'DB' in df.columns and df['DB'].iloc[0] == 'SCOPUS':
            df = clean_scopus_author_fullnames(df)
        cleaned_dataframes.append(df)
    
    # Merge data frames
    M = pd.concat(cleaned_dataframes, ignore_index=True)
    initial_size = len(M)
    
    # Create DB_Original column to track original source databases
    M['DB_Original'] = M['DB']
    
    if remove_duplicated:
        if merge_fields:
            # Group by DOI and select the most complete data within each group
            if 'DI' in M.columns:
                # Group records with DOI
                grouped = M[~M['DI'].isna()].groupby('DI', as_index=False).agg(
                    lambda x: '; '.join(sorted(set(str(val) for val in x if pd.notna(val)))) if x.name == 'DB_Original'
                    else merge_values(x)
                )
                
                # Update DB field for merged records
                grouped.loc[grouped['DB_Original'].str.contains(';'), 'DB'] = 'BIBEXPY'
                
                # Add records without DOI
                no_doi = M[M['DI'].isna()]
                M = pd.concat([grouped, no_doi], ignore_index=True)
            
            # Check duplicates by title and year
            if 'TI' in M.columns and 'PY' in M.columns:
                # Clean titles
                M['clean_title'] = M['TI'].apply(lambda x: re.sub(r'[^a-zA-Z0-9\s]', '', str(x)))
                M['clean_title'] = M['clean_title'].apply(trim)
                
                # Group by title and year
                M['title_year'] = M['clean_title'] + ' ' + M['PY'].astype(str)
                
                # Select the most complete data for each group
                grouped = M.groupby('title_year', as_index=False).agg(
                    lambda x: '; '.join(sorted(set(str(val) for val in x if pd.notna(val)))) if x.name == 'DB_Original'
                    else merge_values(x)
                )
                
                # Update DB field for merged records
                grouped.loc[grouped['DB_Original'].str.contains(';'), 'DB'] = 'BIBEXPY'
                
                M = grouped.drop(['title_year', 'clean_title'], axis=1)
        else:
            # Just remove duplicate records
            if 'DI' in M.columns:
                duplicates = M['DI'].duplicated() & ~M['DI'].isna()
                M = M[~duplicates]
            
            if 'TI' in M.columns and 'PY' in M.columns:
                clean_titles = M['TI'].apply(lambda x: re.sub(r'[^a-zA-Z0-9\s]', '', str(x)))
                clean_titles = clean_titles.apply(trim)
                title_year = clean_titles + ' ' + M['PY'].astype(str)
                duplicates = title_year.duplicated()
                M = M[~duplicates]
    
    # If there are multiple databases
    if len(M['DB'].unique()) > 1:
        # DB'yi ISI'ya set edelim
        M['DB'] = 'ISI'
        
        # Complete WC and SC fields from each other
        if 'WC' in M.columns and 'SC' in M.columns:
            # Fill WC from SC if WC is empty
            M['WC'] = M.apply(lambda row: row['SC'] if (pd.isna(row['WC']) or str(row['WC']).strip() == '') and pd.notna(row['SC']) else row['WC'], axis=1)
            # Fill SC from WC if SC is empty
            M['SC'] = M.apply(lambda row: row['WC'] if (pd.isna(row['SC']) or str(row['SC']).strip() == '') and pd.notna(row['WC']) else row['SC'], axis=1)
        
        # Merge RP data using temporary columns
        if 'RP_WOS' in M.columns and 'RP_SCOPUS' in M.columns:
            M['RP'] = M.apply(lambda row: row['RP_WOS'] if pd.notna(row['RP_WOS']) and str(row['RP_WOS']).strip() 
                             else (row['RP_SCOPUS'] if pd.notna(row['RP_SCOPUS']) and str(row['RP_SCOPUS']).strip() else ''), 
                             axis=1)
            # Drop temporary columns
            M = M.drop(['RP_WOS', 'RP_SCOPUS'], axis=1)
        
        # Clean author data using new merge function
        if 'AU' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'AU']):
                        continue
                        
                    wos_authors = wos_data.at[idx, 'AU'] if idx in wos_data.index else ''
                    scopus_authors = scopus_data.at[idx, 'AU'] if idx in scopus_data.index else ''
                    
                    if wos_authors and scopus_authors:
                        M.at[idx, 'AU'] = merge_author_fields(wos_authors, scopus_authors)
        
        # Clean author full names using WoS format
        if 'AF' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'AF']):
                        continue
                        
                    wos_af = wos_data.at[idx, 'AF'] if idx in wos_data.index else ''
                    scopus_af = scopus_data.at[idx, 'AF'] if idx in scopus_data.index else ''
                    
                    if wos_af and scopus_af:
                        M.at[idx, 'AF'] = merge_author_fullnames(wos_af, scopus_af)
        
        # Use Scopus source title when available, otherwise use WoS
        if 'SO' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    try:
                        # Get source titles from both sources using loc
                        wos_so = wos_data.loc[idx, 'SO'] if idx in wos_data.index else ''
                        scopus_so = scopus_data.loc[idx, 'SO'] if idx in scopus_data.index else ''
                        
                        # Convert NaN to empty string
                        wos_so = '' if pd.isna(wos_so) else str(wos_so)
                        scopus_so = '' if pd.isna(scopus_so) else str(scopus_so)
                        
                        # Merge source titles only if at least one source has data
                        if wos_so or scopus_so:
                            merged_so = merge_source_title(wos_so, scopus_so)
                            if merged_so:
                                M.loc[idx, 'SO'] = merged_so
                    except Exception as e:
                        print(f"Warning: Error merging source titles for index {idx}: {str(e)}")
                        # Use Scopus title if available, otherwise use WoS
                        if pd.notna(scopus_so):
                            M.loc[idx, 'SO'] = scopus_so
                        elif pd.notna(wos_so):
                            M.loc[idx, 'SO'] = wos_so
                        continue
        
        # Use WoS journal abbreviation when available, otherwise use Scopus
        if 'JI' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'JI']):
                        continue
                        
                    wos_ji = wos_data.at[idx, 'JI'] if idx in wos_data.index else ''
                    scopus_ji = scopus_data.at[idx, 'JI'] if idx in scopus_data.index else ''
                    
                    # Prefer WoS format if available
                    if wos_ji and not pd.isna(wos_ji):
                        M.at[idx, 'JI'] = wos_ji
                    elif scopus_ji and not pd.isna(scopus_ji):
                        M.at[idx, 'JI'] = scopus_ji
        
        # Clean addresses using WoS format
        if 'C1' in M.columns:
            # Get WoS and Scopus data
            wos_data = M[M['DB_Original'] == 'ISI'].copy()
            scopus_data = M[M['DB_Original'] == 'SCOPUS'].copy()
            
            # Initialize C1 column if not exists
            if 'C1' not in M.columns:
                M['C1'] = ''
            
            # Process each row
            for idx in M.index:
                try:
                    # Get original database source
                    db_source = M.at[idx, 'DB_Original']
                    
                    # Get addresses based on source
                    if db_source == 'SCOPUS':
                        current_address = scopus_data.at[idx, 'C1'] if idx in scopus_data.index else ''
                    elif db_source == 'ISI':
                        current_address = wos_data.at[idx, 'C1'] if idx in wos_data.index else ''
                    else:
                        current_address = ''
                    
                    # Clean and set the address
                    if pd.notna(current_address) and str(current_address).strip():
                        M.at[idx, 'C1'] = str(current_address).strip()
                    
                except Exception:
                    continue
        
        # Clean and merge abstracts
        if 'AB' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'AB']):
                        continue
                        
                    wos_ab = wos_data.at[idx, 'AB'] if idx in wos_data.index else ''
                    scopus_ab = scopus_data.at[idx, 'AB'] if idx in scopus_data.index else ''
                    
                    if wos_ab or scopus_ab:
                        M.at[idx, 'AB'] = merge_abstracts(wos_ab, scopus_ab)
        
        # Clean and merge author keywords
        if 'DE' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'DE']):
                        continue
                        
                    wos_keywords = wos_data.at[idx, 'DE'] if idx in wos_data.index else ''
                    scopus_keywords = scopus_data.at[idx, 'DE'] if idx in scopus_data.index else ''
                    
                    if wos_keywords or scopus_keywords:
                        M.at[idx, 'DE'] = merge_keywords(wos_keywords, scopus_keywords)
        
        # Clean and merge index keywords
        if 'ID' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'ID']):
                        continue
                        
                    wos_keywords = wos_data.at[idx, 'ID'] if idx in wos_data.index else ''
                    scopus_keywords = scopus_data.at[idx, 'ID'] if idx in scopus_data.index else ''
                    
                    if wos_keywords or scopus_keywords:
                        M.at[idx, 'ID'] = merge_index_keywords(wos_keywords, scopus_keywords)
        
        # Clean and merge references
        if 'CR' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    try:
                        # Get references from both sources
                        wos_refs = wos_data.loc[wos_data.index == idx, 'CR'].iloc[0] if idx in wos_data.index else ''
                        scopus_refs = scopus_data.loc[scopus_data.index == idx, 'CR'].iloc[0] if idx in scopus_data.index else ''
                        
                        # Convert NaN to empty string
                        wos_refs = '' if pd.isna(wos_refs) else str(wos_refs)
                        scopus_refs = '' if pd.isna(scopus_refs) else str(scopus_refs)
                        
                        # Merge references only if at least one source has data
                        if wos_refs or scopus_refs:
                            merged_refs = merge_references(wos_refs, scopus_refs)
                            if merged_refs:
                                M.at[idx, 'CR'] = merged_refs
                    except Exception as e:
                        print(f"Error merging references for index {idx}: {str(e)}")
                        continue
        
        # Clean and merge publisher names
        if 'PU' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    if pd.isna(M.at[idx, 'PU']):
                        continue
                        
                    wos_pub = wos_data.at[idx, 'PU'] if idx in wos_data.index else ''
                    scopus_pub = scopus_data.at[idx, 'PU'] if idx in scopus_data.index else ''
                    
                    if wos_pub or scopus_pub:
                        M.at[idx, 'PU'] = merge_publisher(wos_pub, scopus_pub)
        
        # Clean and merge language information
        if 'LA' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    wos_lang = wos_data.at[idx, 'LA'] if idx in wos_data.index else ''
                    scopus_lang = scopus_data.at[idx, 'LA'] if idx in scopus_data.index else ''
                    
                    M.at[idx, 'LA'] = merge_language(wos_lang, scopus_lang)
        
        # Clean and merge document types
        if 'DT' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    try:
                        # Get document types from both sources using loc
                        wos_dt = wos_data.loc[idx, 'DT'] if idx in wos_data.index else ''
                        scopus_dt = scopus_data.loc[idx, 'DT'] if idx in scopus_data.index else ''
                        
                        # Convert NaN to empty string
                        wos_dt = '' if pd.isna(wos_dt) else str(wos_dt)
                        scopus_dt = '' if pd.isna(scopus_dt) else str(scopus_dt)
                        
                        # Merge document types only if at least one source has data
                        if wos_dt or scopus_dt:
                            merged_dt = merge_document_type(wos_dt, scopus_dt)
                            if merged_dt:
                                M.loc[idx, 'DT'] = merged_dt
                    except Exception as e:
                        print(f"Warning: Error merging document types for index {idx}: {str(e)}")
                        # Use any available document type in case of error
                        if pd.notna(wos_dt):
                            M.loc[idx, 'DT'] = wos_dt
                        elif pd.notna(scopus_dt):
                            M.loc[idx, 'DT'] = scopus_dt
                        continue
        
        # Clean and merge unique identifiers
        if 'UT' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    # WoS verisi varsa onu kullan
                    if idx in wos_data.index and pd.notna(wos_data.at[idx, 'UT']):
                        M.at[idx, 'UT'] = wos_data.at[idx, 'UT']
                    # WoS verisi yoksa ve Scopus verisi varsa Scopus'u kullan
                    elif idx in scopus_data.index and pd.notna(scopus_data.at[idx, 'UT']):
                        M.at[idx, 'UT'] = scopus_data.at[idx, 'UT']
        
        # Clean and merge URLs
        if 'URL' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    wos_url = wos_data.at[idx, 'URL'] if idx in wos_data.index else ''
                    scopus_url = scopus_data.at[idx, 'URL'] if idx in scopus_data.index else ''
                    
                    M.at[idx, 'URL'] = merge_url(wos_url, scopus_url)
        
        # Clean and merge Open Access status
        if 'OA' in M.columns:
            wos_data = M[M['DB_Original'] == 'ISI']
            scopus_data = M[M['DB_Original'] == 'SCOPUS']
            
            if not wos_data.empty and not scopus_data.empty:
                for idx in M.index:
                    wos_oa = wos_data.at[idx, 'OA'] if idx in wos_data.index else ''
                    scopus_oa = scopus_data.at[idx, 'OA'] if idx in scopus_data.index else ''
                    
                    M.at[idx, 'OA'] = merge_open_access(wos_oa, scopus_oa)
    
    # Create SR tag
    M = meta_tag_extraction(M, 'SR')
    
    return M

def main():
    try:
        print("Database Merge Tool")
        print("------------------")
        
        # Find Excel files in rawData folder
        raw_data_path = "rawData"
        if not os.path.exists(raw_data_path):
            print("Error: rawData folder not found!")
            return
        
        excel_files = [f for f in os.listdir(raw_data_path) if f.endswith('.xlsx')]
        
        if not excel_files:
            print("Error: No Excel files found in rawData folder!")
            return
        
        print(f"\nFound Excel files:")
        for i, file in enumerate(excel_files, 1):
            print(f"{i}. {file}")
        
        dataframes = []
        for file in excel_files:
            try:
                file_path = os.path.join(raw_data_path, file)
                df = pd.read_excel(file_path)
                dataframes.append(df)
                print(f"\n{file} loaded successfully.")
                print(f"Record count: {len(df)}")
            except Exception as e:
                print(f"Error: Could not read file {file}: {str(e)}")
        
        if not dataframes:
            print("\nNo files could be read!")
            return
        
        print("\nMerging data...")
        merged_df = merge_db_sources(*dataframes, remove_duplicated=True, merge_fields=True, verbose=False)
        
        # Save merged file
        output_file = "merged_data.xlsx"
        merged_df.to_excel(output_file, index=True)  # SR will be used as index
        
        print(f"\nMerged data saved to {output_file}")
        print(f"Total record count: {len(merged_df)}")
        
    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}")
    finally:
        print("\nProgram terminating...")

if __name__ == "__main__":
    main() 