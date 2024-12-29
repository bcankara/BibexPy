import requests
import pandas as pd
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

def extract_metadata_from_crossref(doi: str) -> dict:
    """Extract metadata from CrossRef API (Free, no key required)"""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        headers = {
            'User-Agent': 'keyFINDER/1.0 (mailto:your-email@example.com)'
        }
        response = requests.get(url, headers=headers)
        
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
            
            # Authors
            if 'author' in work:
                authors = [f"{a.get('given', '')} {a.get('family', '')}" for a in work['author']]
                metadata['AU'] = '; '.join(authors)
            
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
            
            # Subject Categories
            if 'subject' in work:
                subjects = work['subject']
                metadata['SC'] = '; '.join(subjects)  # Science Categories
                metadata['WC'] = '; '.join(subjects)  # Web of Science Categories
                metadata['DE'] = '; '.join(subjects)  # Keywords/Descriptors
            
            # License
            if 'license' in work and work['license']:
                license_urls = [lic.get('URL', '') for lic in work['license']]
                metadata['LI'] = '; '.join(filter(None, license_urls))
            
            return metadata
    except Exception as e:
        print(f"CrossRef API Error: {str(e)}")
    return {}

def extract_metadata_from_openalex(doi: str) -> dict:
    """Extract metadata from OpenAlex API (Free, no key required)"""
    try:
        url = f"https://api.openalex.org/works/doi:{doi}"
        response = requests.get(url)
        
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
                
                for authorship in work['authorships']:
                    if 'author' in authorship and 'display_name' in authorship['author']:
                        author_name = authorship['author']['display_name']
                        authors.append(author_name)
                        
                        # Get institutions for this author
                        if 'institutions' in authorship:
                            author_insts = []
                            for inst in authorship['institutions']:
                                if 'display_name' in inst:
                                    inst_name = inst['display_name']
                                    institutions.append(inst_name)
                                    author_insts.append(inst_name)
                            
                            if author_insts:
                                author_institutions.append(f"{author_name} [{'; '.join(author_insts)}]")
                
                if authors:
                    metadata['AU'] = '; '.join(authors)
                
                # Unique institutions
                if institutions:
                    metadata['C1'] = '; '.join(list(set(institutions)))
                
                # Author-Institution pairs
                if author_institutions:
                    metadata['AF'] = '; '.join(author_institutions)
            
            # Publication Year
            if 'publication_year' in work:
                metadata['PY'] = work['publication_year']
            
            # Journal/Publisher Information
            if 'host_venue' in work:
                venue = work['host_venue']
                if 'display_name' in venue:
                    metadata['SO'] = venue['display_name']
                if 'publisher' in venue:
                    metadata['PU'] = venue['publisher']
                if 'issn_l' in venue:
                    metadata['SN'] = venue['issn_l']
                if 'url' in venue:
                    metadata['UR'] = venue['url']
            
            # Abstract
            if 'abstract' in work:
                metadata['AB'] = work['abstract']
            
            # Concepts/Keywords
            if 'concepts' in work:
                concepts = [c['display_name'] for c in work['concepts'] if 'display_name' in c]
                if concepts:
                    metadata['SC'] = '; '.join(concepts)  # Science Categories
                    metadata['WC'] = '; '.join(concepts)  # Web of Science Categories
                    metadata['DE'] = '; '.join(concepts)  # Keywords/Descriptors
            
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
            
            # Referenced Works
            if 'referenced_works' in work:
                metadata['CR'] = '; '.join(work['referenced_works'])
            
            return metadata
    except Exception as e:
        print(f"OpenAlex API Error: {str(e)}")
    return {}

def extract_metadata_from_scopus(doi: str, api_key: str) -> dict:
    """Extract metadata from Scopus API (Requires API key)"""
    try:
        url = f"https://api.elsevier.com/content/abstract/doi/{doi}"
        headers = {
            'X-ELS-APIKey': api_key,
            'Accept': 'application/json'
        }
        response = requests.get(url, headers=headers)
        
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
                        metadata['C1'] = '; '.join(list(set(affiliations)))
                    if author_affiliations:
                        metadata['AF'] = '; '.join(author_affiliations)
                
                # Subject Areas / Keywords
                if 'subject-areas' in work and 'subject-area' in work['subject-areas']:
                    subjects = []
                    for subject in work['subject-areas']['subject-area']:
                        if '$' in subject:
                            subjects.append(subject['$'])
                    if subjects:
                        metadata['SC'] = '; '.join(subjects)  # Science Categories
                        metadata['WC'] = '; '.join(subjects)  # Web of Science Categories
                        metadata['DE'] = '; '.join(subjects)  # Keywords
                
                return metadata
    except Exception as e:
        print(f"Scopus API Error: {str(e)}")
    return {}

def extract_metadata_from_datacite(doi: str) -> dict:
    """Extract metadata from DataCite API (Free, no key required)"""
    try:
        url = f"https://api.datacite.org/dois/{doi}"
        headers = {
            'Accept': 'application/vnd.api+json'
        }
        response = requests.get(url, headers=headers)
        
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
                    # Tüm subjects'leri al
                    all_subjects = [subj.get('subject', '') for subj in work['subjects'] if 'subject' in subj]
                    if all_subjects:
                        # Use all subjects for Keywords
                        metadata['DE'] = '; '.join(all_subjects)
                        
                        # If more than 2 elements, add only to Keywords
                        if len(all_subjects) <= 2:
                            # Use categories for SC and WC
                            metadata['SC'] = '; '.join(all_subjects)
                            metadata['WC'] = '; '.join(all_subjects)
                
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

def extract_metadata_from_unpaywall(doi: str, email: str) -> dict:
    """Extract metadata from Unpaywall API (Free, requires email)"""
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        response = requests.get(url)
        
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

def extract_metadata_from_europepmc(doi: str) -> dict:
    """Extract metadata from Europe PMC API (Free)"""
    try:
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json"
        response = requests.get(url)
        
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

def extract_metadata_from_semantic_scholar(doi: str, api_key: str = None) -> dict:
    """Extract metadata from Semantic Scholar API (Free, API key optional but recommended)"""
    try:
        url = f"https://api.semanticscholar.org/v1/paper/{doi}"
        headers = {'x-api-key': api_key} if api_key else {}
        
        response = requests.get(url, headers=headers)
        
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
            
            # Fields of Study
            if 'fieldsOfStudy' in work:
                fields = work['fieldsOfStudy']
                if fields:
                    metadata['SC'] = '; '.join(fields)  # Science Categories
                    metadata['WC'] = '; '.join(fields)  # Web of Science Categories
                    metadata['DE'] = '; '.join(fields)  # Keywords/Descriptors
            
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

def extract_metadata(doi: str, current_data: dict, scopus_api_key: str = None, semantic_scholar_key: str = None, unpaywall_email: str = None) -> dict:
    """Try to extract metadata from multiple sources"""
    metadata = current_data.copy()
    api_sources = {}  # Hangi alanın hangi API'den geldiğini takip etmek için
    
    try:
        # API anahtarlarını .env'den al
        scopus_api_key = os.getenv('SCOPUS_API_KEY', scopus_api_key)
        unpaywall_email = os.getenv('UNPAYWALL_EMAIL', unpaywall_email)

        if not scopus_api_key:
            print("\nWarning: Scopus API key not found in .env file.")
            print("You can add it to the .env file to enable Scopus metadata enrichment.")
            print("Continuing with other data sources...")

        if not unpaywall_email:
            print("\nWarning: Unpaywall email not found in .env file.")
            print("You can add it to the .env file to enable Unpaywall metadata enrichment.")
            print("Continuing with other data sources...")
        
        # CrossRef
        print(f"\nTrying CrossRef API...", end='')
        try:
            crossref_data = extract_metadata_from_crossref(doi)
            if crossref_data:
                for key, value in crossref_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
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
            openalex_data = extract_metadata_from_openalex(doi)
            if openalex_data:
                for key, value in openalex_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'OpenAlex'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
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
                                         headers={'X-ELS-APIKey': scopus_api_key, 'Accept': 'application/json'})
                    print(f" [NO DATA - Status: {response.status_code}, Response: {response.text[:100]}...]")
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
        
        # DataCite
        print(f"Trying DataCite API...", end='')
        try:
            response = requests.get(f"https://api.datacite.org/dois/{doi}", headers={'Accept': 'application/vnd.api+json'})
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
            response = requests.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json")
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
        
        return metadata
        
    except Exception as e:
        print(f"Error processing DOI {doi}: {str(e)}")
        return current_data 