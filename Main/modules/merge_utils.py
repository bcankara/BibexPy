import pandas as pd
from time import sleep
import threading
import sys
from datetime import datetime
import os
from colorama import init, Fore, Style
from .api_utils import extract_metadata
from .file_utils import save_statistics, save_comprehensive_statistics

# Initialize colorama
init()

class ProcessIndicator:
    """Process indicator"""
    busy = False
    delay = 0.1
    
    @staticmethod
    def indicator():
        while 1:
            for cursor in '|/-\\':
                yield cursor
    
    def __init__(self, description="Processing"):
        self.description = description
        self.generator = self.indicator()
        self.busy = False
        self.visible = False
    
    def write_next(self):
        with self._screen_lock:
            if not self.visible:
                sys.stdout.write(f"{self.description} ")
                self.visible = True
            sys.stdout.write(next(self.generator))
            sys.stdout.flush()
            sys.stdout.write('\b')
    
    def remove_spinner(self, cleanup=False):
        with self._screen_lock:
            if self.visible:
                sys.stdout.write('\b')
                self.visible = False
                if cleanup:
                    sys.stdout.write(f"\r{self.description} Completed\n")
                    sys.stdout.flush()
    
    def spinner_task(self):
        while self.busy:
            self.write_next()
            sleep(self.delay)
    
    def __enter__(self):
        if sys.stdout.isatty():
            self._screen_lock = threading.Lock()
            self.busy = True
            self.thread = threading.Thread(target=self.spinner_task)
            self.thread.start()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if sys.stdout.isatty():
            self.busy = False
            sleep(self.delay)
            self.remove_spinner(cleanup=True)

def complete_category_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Complete SC and WC fields from each other"""
    with ProcessIndicator("Completing category fields") as spinner:
        completed = 0
        
        for idx, row in df.iterrows():
            sc_empty = pd.isna(row.get('SC')) or str(row.get('SC')).strip() == ''
            wc_empty = pd.isna(row.get('WC')) or str(row.get('WC')).strip() == ''
            
            if sc_empty and not wc_empty:  # SC empty, WC filled
                df.loc[idx, 'SC'] = row['WC']
                completed += 1
            elif wc_empty and not sc_empty:  # WC empty, SC filled
                df.loc[idx, 'WC'] = row['SC']
                completed += 1
    
    return df

def merge_databases_simple(wos_df: pd.DataFrame, scopus_df: pd.DataFrame, output_file: str, result_dir: str = None) -> tuple[bool, dict, pd.DataFrame]:
    """Simple database merge with deduplication"""
    try:
        from Main.MergeDB import merge_db_sources
        
        if result_dir is None:
            result_dir = os.path.dirname(output_file)
        
        # Define output files
        merged_file = os.path.join(result_dir, "Merged_Simple.xlsx")
        stats_txt = os.path.join(result_dir, "Statistic.txt")
        stats_xlsx = os.path.join(result_dir, "Statistic.xlsx")
        lost_wos_file = os.path.join(result_dir, "Lost_Wos_Records.xlsx")
        lost_scopus_file = os.path.join(result_dir, "Lost_Scopus_Records.xlsx")
        
        # Add database labels
        wos_df['DB'] = 'ISI'
        scopus_df['DB'] = 'SCOPUS'
        
        print(f"\n{Fore.CYAN}Starting database merge...{Style.RESET_ALL}")
        merged_df = merge_db_sources(wos_df, scopus_df, remove_duplicated=True, merge_fields=False)
        
        # Calculate and print statistics
        total_docs = len(wos_df) + len(scopus_df)
        merged_docs = len(merged_df)
        duplicates = total_docs - merged_docs
        
        print_stats(f"Completeness of metadata -- {merged_docs} records merged from 2 databases")
        print_stats(f"Original size -- Total {total_docs} records, {duplicates} duplicates merged")
        
        # Save results
        print(f"\n{Fore.CYAN}Saving data...{Style.RESET_ALL}")
        merged_df.to_excel(merged_file, index=False)
        print(f"{Fore.GREEN}Save completed: {merged_file}{Style.RESET_ALL}")
        
        # Save statistics
        stats = {
            'Total Records': len(merged_df),
            'WoS Records': len(wos_df),
            'Scopus Records': len(scopus_df),
            'Merged Columns': len(merged_df.columns),
            'Common Columns': len(set(wos_df.columns) & set(scopus_df.columns))
        }
        
        save_statistics(stats, stats_txt)
        field_stats = save_comprehensive_statistics(stats, wos_df, scopus_df, merged_df, stats_xlsx, stage="Basic")
        
        # Analyze and save lost records
        lost_wos, lost_scopus, loss_reasons = analyze_lost_records(wos_df, scopus_df, merged_df)
        if not lost_wos.empty:
            lost_wos.to_excel(lost_wos_file, index=False)
        if not lost_scopus.empty:
            lost_scopus.to_excel(lost_scopus_file, index=False)
        
        print(f"\n{Fore.GREEN}Analysis results saved in: {result_dir}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Generated Files:{Style.RESET_ALL}")
        print(f"1. {Fore.WHITE}Merged Data: {Fore.GREEN}Merged_Simple.xlsx{Style.RESET_ALL}")
        print(f"2. {Fore.WHITE}Statistics: {Fore.GREEN}Statistic_Basic.xlsx{Style.RESET_ALL}")
        print(f"3. {Fore.WHITE}Basic Statistics: {Fore.GREEN}Statistic.txt{Style.RESET_ALL}")
        if not lost_wos.empty:
            print(f"4. {Fore.WHITE}Lost WoS Records: {Fore.YELLOW}Lost_Wos_Records.xlsx{Style.RESET_ALL}")
        if not lost_scopus.empty:
            print(f"5. {Fore.WHITE}Lost Scopus Records: {Fore.YELLOW}Lost_Scopus_Records.xlsx{Style.RESET_ALL}")
        
        return True, stats, merged_df
    except Exception as e:
        print(f"\n{Fore.RED}Error: {str(e)}{Style.RESET_ALL}")
        return False, {}, pd.DataFrame()

def print_stats(message: str):
    """Print statistics message"""
    if "Completeness" in message:
        parts = message.split("--")
        if len(parts) == 2:
            print(f"\n{Fore.CYAN}Merge Summary:{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}{parts[1].strip()}{Style.RESET_ALL}")
    elif "Original size" in message:
        parts = message.split("--")
        if len(parts) == 2:
            print(f"  {Fore.YELLOW}{parts[1].strip()}{Style.RESET_ALL}")
            print()

def print_field_stats(field: str, stats: dict):
    """Print field-based statistics"""
    print(f"\n{Fore.CYAN}Field: {Fore.WHITE}{field}{Style.RESET_ALL}")
    
    # Calculate improvement percentage
    improvement = float(stats['Improvement Rate'].rstrip('%'))
    if improvement > 0:
        improvement_color = Fore.GREEN
    elif improvement < 0:
        improvement_color = Fore.RED
    else:
        improvement_color = Fore.YELLOW
        
    print(f"  Empty Cells: {Fore.YELLOW}{stats['Initial Empty']}{Style.RESET_ALL} -> {Fore.GREEN}{stats['Final Empty']}{Style.RESET_ALL} ({improvement_color}{stats['Improvement Rate']} improvement{Style.RESET_ALL})")
    print(f"  Unique Values: {Fore.YELLOW}{stats['Initial Unique']}{Style.RESET_ALL} -> {Fore.GREEN}{stats['Final Unique']}{Style.RESET_ALL} ({Fore.CYAN}{stats['Enrichment']} change{Style.RESET_ALL})")

def create_result_folder(base_dir: str) -> str:
    """Create a unique result folder with timestamp"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_dir = os.path.join(base_dir, f"Analysis_{timestamp}")
    os.makedirs(result_dir, exist_ok=True)
    return result_dir

def enrich_with_api(merged_df: pd.DataFrame, scopus_api_key: str = None, semantic_scholar_key: str = None, 
                  unpaywall_email: str = None, api_log: str = None, api_updates: str = None, 
                  result_dir: str = None) -> tuple[bool, dict, pd.DataFrame]:
    """Metadata enrichment process using APIs"""
    try:
        # Define output files
        cell_files_dir = os.path.join(result_dir, "Cell_Files")
        text_files_dir = os.path.join(result_dir, "Text_Files")
        
        api_log_file = os.path.join(result_dir, "Api_Log.txt") if api_log is None else api_log
        api_updates_file = os.path.join(result_dir, "Api_Update.xlsx") if api_updates is None else api_updates
        api_enriched_bib = os.path.join(cell_files_dir, "Merged_API_Enriched_Bib.xlsx")
        api_enriched_vos = os.path.join(text_files_dir, "Merged_API_Enriched_Vos.txt")
        stats_xlsx = os.path.join(result_dir, "Statistics_API.xlsx")
        
        # Create DataFrame to track API updates
        api_updates_df = pd.DataFrame(columns=['DOI', 'Field', 'Value', 'API_Source'])
        
        # API supported fields and which APIs support which fields
        api_supported_fields = {
            'DT': {'name': 'Document Type', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC']},
            'TI': {'name': 'Title', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC', 'SemanticScholar']},
            'AU': {'name': 'Author', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC', 'SemanticScholar']},
            'AF': {'name': 'Author-Affiliation', 'apis': ['OpenAlex']},
            'C1': {'name': 'Author Addresses', 'apis': ['OpenAlex', 'DataCite']},
            'PY': {'name': 'Publication Year', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC', 'SemanticScholar']},
            'SO': {'name': 'Publication Name', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC']},
            'JI': {'name': 'ISO Source Abbreviation', 'apis': ['Scopus']},
            'VL': {'name': 'Volume', 'apis': ['Scopus', 'EuropePMC']},
            'IS': {'name': 'Issue', 'apis': ['Scopus', 'EuropePMC']},
            'BP': {'name': 'Beginning Page', 'apis': ['Scopus', 'EuropePMC']},
            'EP': {'name': 'Ending Page', 'apis': ['Scopus', 'EuropePMC']},
            'PU': {'name': 'Publisher', 'apis': ['CrossRef', 'OpenAlex', 'Scopus']},
            'PA': {'name': 'Publisher Address', 'apis': ['CrossRef']},
            'SN': {'name': 'ISSN', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'EuropePMC']},
            'AB': {'name': 'Abstract', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'DataCite', 'EuropePMC', 'SemanticScholar']},
            'DE': {'name': 'Author Keywords', 'apis': ['CrossRef', 'DataCite', 'SemanticScholar']},
            'ID': {'name': 'Keywords Plus', 'apis': ['DataCite']},
            'SC': {'name': 'Research Areas', 'apis': ['CrossRef', 'DataCite', 'SemanticScholar']},
            'WC': {'name': 'Web of Science Categories', 'apis': ['CrossRef', 'DataCite', 'SemanticScholar']},
            'TC': {'name': 'Times Cited', 'apis': ['OpenAlex', 'Scopus', 'EuropePMC', 'SemanticScholar']},
            'RC': {'name': 'Reference Count', 'apis': ['SemanticScholar']},
            'CR': {'name': 'Cited References', 'apis': ['OpenAlex', 'Scopus', 'EuropePMC', 'SemanticScholar']},
            'OA': {'name': 'Open Access', 'apis': ['OpenAlex', 'EuropePMC']},
            'LI': {'name': 'License', 'apis': ['CrossRef']},
            'UR': {'name': 'URL', 'apis': ['CrossRef', 'OpenAlex', 'Scopus', 'EuropePMC', 'SemanticScholar']},
            'AI': {'name': 'Additional Information', 'apis': ['EuropePMC']},
            'EI': {'name': 'External IDs', 'apis': ['SemanticScholar']}
        }
        
        print(f"\n{Fore.CYAN}Checking empty fields in supported areas:{Style.RESET_ALL}")
        print("-" * 50)
        print(f"{Fore.WHITE}Field Code | Field Name | Supporting APIs | Empty Records{Style.RESET_ALL}")
        print("-" * 50)
        
        # Show empty field statistics
        empty_stats = {}
        dois_with_empty_fields = set()
        
        for field_code, field_info in api_supported_fields.items():
            if field_code in merged_df.columns:
                empty_mask = (merged_df[field_code].isna()) | (merged_df[field_code].astype(str).str.strip() == '')
                empty_count = empty_mask.sum()
                empty_percentage = (empty_count / len(merged_df)) * 100
                
                # Choose color based on empty percentage
                if empty_percentage == 0:
                    status_color = Fore.GREEN
                elif empty_percentage < 10:
                    status_color = Fore.CYAN
                elif empty_percentage < 30:
                    status_color = Fore.YELLOW
                else:
                    status_color = Fore.RED
                
                print(f"{Fore.WHITE}{field_code:^10} | {field_info['name']:<30} | {', '.join(field_info['apis']):<50} | {status_color}{empty_count:>5} ({empty_percentage:.2f}%){Style.RESET_ALL}")
                
                empty_stats[field_code] = {
                    'Initial Empty': empty_count,
                    'Initial %': empty_percentage,
                    'Supporting APIs': field_info['apis']
                }
                
                if empty_count > 0:
                    valid_dois = merged_df.loc[
                        empty_mask & 
                        merged_df['DI'].notna() & 
                        (merged_df['DI'].astype(str).str.strip() != ''), 
                        'DI'
                    ].tolist()
                    dois_with_empty_fields.update(valid_dois)
        
        print("-" * 50)
        total_enriched = 0
        total_dois = len(dois_with_empty_fields)
        print(f"\n{Fore.GREEN}Found {total_dois} DOIs with empty fields that can be enriched{Style.RESET_ALL}")
        print("-" * 50)
        
        # For each DOI, make one API query
        for i, doi in enumerate(dois_with_empty_fields, 1):
            try:
                if pd.isna(doi) or str(doi).strip() == '':
                    continue
                    
                idx = merged_df[merged_df['DI'] == doi].index[0]
                db_source = merged_df.loc[idx, 'DB']
                
                log_msg = f"\n{Fore.CYAN}Processing DOI ({i}/{total_dois}): {Fore.WHITE}{doi}{Style.RESET_ALL}"
                print(log_msg)
                print(f"{Fore.YELLOW}Source Database: {db_source}{Style.RESET_ALL}")
                
                if api_log:
                    with open(api_log_file, "a", encoding="utf-8") as f:
                        f.write(f"{log_msg}\n")
                        f.write(f"Source Database: {db_source}\n")
                
                # Show empty fields
                empty_fields = []
                for field_code, field_info in api_supported_fields.items():
                    if field_code in merged_df.columns:
                        current_value = merged_df.loc[idx, field_code]
                        if pd.isna(current_value) or str(current_value).strip() == '':
                            empty_fields.append(f"{field_info['name']} ({field_code}) - Supported by: {', '.join(field_info['apis'])}")
                
                print(f"\n{Fore.YELLOW}Empty Fields:{Style.RESET_ALL}")
                for field in empty_fields:
                    print(f"  - {field}")
                
                # Clear previous API data
                current_data = {
                    'DI': doi,
                    'DB': db_source
                }
                
                print(f"\n{Fore.CYAN}Querying APIs...{Style.RESET_ALL}")
                row = extract_metadata(doi, current_data, scopus_api_key)
                
                if 'API_Sources' in row:
                    print(f"\n{Fore.GREEN}API Sources Used:{Style.RESET_ALL}")
                    for field, source in row['API_Sources'].items():
                        print(f"  - {field_info['name']} ({field}): {source}")
                
                # Update empty fields
                updated_fields = []
                for field_code, field_info in api_supported_fields.items():
                    if field_code in merged_df.columns:
                        current_value = merged_df.loc[idx, field_code]
                        if (pd.isna(current_value) or str(current_value).strip() == '') and field_code in row and pd.notna(row[field_code]) and str(row[field_code]).strip() != '':
                            new_value = row[field_code]
                            api_source = row.get('API_Sources', {}).get(field_code, 'Unknown API')
                            
                            merged_df.loc[idx, field_code] = new_value
                            updated_fields.append(f"{field_info['name']} ({field_code}) from {api_source}")
                            
                            api_updates_df = pd.concat([api_updates_df, pd.DataFrame({
                                'DOI': [doi],
                                'Field': [field_code],
                                'Value': [new_value],
                                'API_Source': [api_source]
                            })], ignore_index=True)
                
                if updated_fields:
                    total_enriched += 1
                    print(f"\n{Fore.GREEN}Updated Fields:{Style.RESET_ALL}")
                    for field in updated_fields:
                        print(f"  + {field}")
                    if api_log:
                        with open(api_log_file, "a", encoding="utf-8") as f:
                            f.write("Updated Fields:\n")
                            for field in updated_fields:
                                f.write(f"  + {field}\n")
                else:
                    print(f"\n{Fore.YELLOW}No fields were updated{Style.RESET_ALL}")
                
                print("-" * 50)
                
                if i % 10 == 0 and not api_updates_df.empty:
                    api_updates_df.to_excel(api_updates_file, index=False)
            
            except Exception as e:
                print(f"{Fore.RED}Error processing DOI {doi}: {str(e)}{Style.RESET_ALL}")
                if api_log:
                    with open(api_log_file, "a", encoding="utf-8") as f:
                        f.write(f"Error processing DOI {doi}: {str(e)}\n")
                continue
        
        if not api_updates_df.empty:
            api_updates_df.to_excel(api_updates_file, index=False)
        
        print(f"\n{Fore.CYAN}Field Enrichment Summary:{Style.RESET_ALL}")
        print("-" * 100)
        print(f"{Fore.WHITE}Field Code | Field Name | Supporting APIs | Initial Empty | Final Empty | Improvement{Style.RESET_ALL}")
        print("-" * 100)
        
        for field_code, field_info in api_supported_fields.items():
            if field_code in merged_df.columns:
                empty_mask = (merged_df[field_code].isna()) | (merged_df[field_code].astype(str).str.strip() == '')
                final_empty = empty_mask.sum()
                final_percentage = (final_empty / len(merged_df)) * 100
                
                initial_empty = empty_stats[field_code]['Initial Empty']
                initial_percentage = empty_stats[field_code]['Initial %']
                
                improvement = initial_empty - final_empty
                improvement_rate = ((initial_empty - final_empty) / initial_empty * 100) if initial_empty > 0 else 0
                
                print(f"{field_code:^10} | {field_info['name']:<30} | {', '.join(field_info['apis']):<50} | {initial_empty:>5} ({initial_percentage:.2f}%) | {final_empty:>5} ({final_percentage:.2f}%) | {improvement:>5} ({improvement_rate:.2f}%)")
        
        print("-" * 100)
        
        # Save API enriched data
        print("\nSaving API enriched data...")
        merged_df.to_excel(api_enriched_bib, index=False)
        print(f"Save completed: {api_enriched_bib}")
        
        # Convert to VosViewer format
        print("\nConverting data to VosViewer format...")
        from Main.xlsx2vos import convert_excel_to_wos
        convert_excel_to_wos(api_enriched_bib, api_enriched_vos)
        print("VosViewer conversion completed.")
        
        # Prepare statistics
        stats = {
            'Total Records': len(merged_df),
            'Enriched Records': total_enriched,
            'Total Empty Fields': total_dois,
            'Completed Metadata': total_enriched,
            'Field Statistics': empty_stats
        }
        
        # Save comprehensive statistics
        field_stats = save_comprehensive_statistics(stats, None, None, merged_df, stats_xlsx)
        
        print(f"\nAPI enrichment completed. {total_enriched} records updated.")
        print(f"API enrichment results saved to: {result_dir}")
        print("Generated Files:")
        print(f"1. API Enriched Data (Cell_Files): {os.path.basename(api_enriched_bib)}")
        print(f"2. API Enriched Data (Text_Files): {os.path.basename(api_enriched_vos)}")
        print(f"3. API Updates: {os.path.basename(api_updates_file)}")
        print(f"4. API Log: {os.path.basename(api_log_file)}")
        print(f"5. Statistics: {os.path.basename(stats_xlsx)}")
        
        return True, stats, merged_df
    except Exception as e:
        print(f"\nError: {str(e)}")
        return False, {}, pd.DataFrame()

def merge_databases_enhanced(wos_df: pd.DataFrame, scopus_df: pd.DataFrame, output_file: str, 
                           result_dir: str = None) -> tuple[bool, dict, pd.DataFrame]:
    """Enhanced database merge with deduplication and metadata enrichment"""
    try:
        from Main.MergeDB import merge_db_sources
        
        if result_dir is None:
            result_dir = os.path.dirname(output_file)
        
        # Add database labels
        wos_df['DB'] = 'ISI'
        scopus_df['DB'] = 'SCOPUS'
        
        print(f"\n{Fore.CYAN}Starting enhanced database merge...{Style.RESET_ALL}")
        merged_df = merge_db_sources(wos_df, scopus_df, remove_duplicated=True, merge_fields=True)
        
        # Calculate and print statistics
        total_docs = len(wos_df) + len(scopus_df)
        merged_docs = len(merged_df)
        duplicates = total_docs - merged_docs
        
        print_stats(f"Completeness of metadata -- {merged_docs} records merged from 2 databases")
        print_stats(f"Original size -- Total {total_docs} records, {duplicates} duplicates merged")
        
        # Complete category fields (SC and WC)
        merged_df = complete_category_fields(merged_df)
        
        # Save results
        print(f"\n{Fore.CYAN}Saving data...{Style.RESET_ALL}")
        merged_df.to_excel(output_file, index=False)
        print(f"{Fore.GREEN}Save completed: {output_file}{Style.RESET_ALL}")
        
        # Save statistics
        stats = {
            'Total Records': len(merged_df),
            'WoS Records': len(wos_df),
            'Scopus Records': len(scopus_df),
            'Merged Columns': len(merged_df.columns),
            'Common Columns': len(set(wos_df.columns) & set(scopus_df.columns))
        }
        
        return True, stats, merged_df
    except Exception as e:
        print(f"\n{Fore.RED}Error: {str(e)}{Style.RESET_ALL}")
        return False, {}, pd.DataFrame()

def analyze_lost_records(wos_df: pd.DataFrame, scopus_df: pd.DataFrame, merged_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Analyze lost records and determine reasons for loss"""
    # Compare DOIs
    wos_dois = set(wos_df['DOI'].dropna()) if 'DOI' in wos_df.columns else set()
    scopus_dois = set(scopus_df['DOI'].dropna()) if 'DOI' in scopus_df.columns else set()
    merged_dois = set(merged_df['DOI'].dropna()) if 'DOI' in merged_df.columns else set()

    lost_wos = wos_dois - merged_dois
    lost_scopus = scopus_dois - merged_dois

    # Get lost records
    lost_wos_records = wos_df[wos_df['DOI'].isin(lost_wos)] if 'DOI' in wos_df.columns else pd.DataFrame()
    lost_scopus_records = scopus_df[scopus_df['DOI'].isin(lost_scopus)] if 'DOI' in scopus_df.columns else pd.DataFrame()

    # Analyze loss reasons
    loss_reasons = {
        'WoS Loss Reasons': {
            'Total Lost Records': len(lost_wos),
            'Missing DOI': len(wos_df[wos_df['DOI'].isna()]) if 'DOI' in wos_df.columns else 0,
            'Invalid DOI': len(lost_wos_records[lost_wos_records['DOI'].str.contains(r'[^\x00-\x7F]', na=False)]) if not lost_wos_records.empty and 'DOI' in lost_wos_records.columns else 0,
        },
        'Scopus Loss Reasons': {
            'Total Lost Records': len(lost_scopus),
            'Missing DOI': len(scopus_df[scopus_df['DOI'].isna()]) if 'DOI' in scopus_df.columns else 0,
            'Invalid DOI': len(lost_scopus_records[lost_scopus_records['DOI'].str.contains(r'[^\x00-\x7F]', na=False)]) if not lost_scopus_records.empty and 'DOI' in lost_scopus_records.columns else 0,
        }
    }

    return lost_wos_records, lost_scopus_records, loss_reasons 

def save_comprehensive_statistics(stats: dict, wos_df: pd.DataFrame, scopus_df: pd.DataFrame, merged_df: pd.DataFrame, output_file: str) -> dict:
    """Save comprehensive statistics including field-level analysis"""
    field_stats = {}
    total_records = len(merged_df)
    
    # For each field, calculate statistics
    for field in merged_df.columns:
        if field != 'DB':  # Skip DB field
            empty_count = merged_df[field].isna().sum() + (merged_df[field].astype(str).str.strip() == '').sum()
            empty_percentage = (empty_count / total_records) * 100 if total_records > 0 else 0
            
            # Determine status
            if empty_percentage == 0:
                status = "Excellent"
            elif empty_percentage < 1:
                status = "Very Good"
            elif empty_percentage < 5:
                status = "Good"
            elif empty_percentage < 10:
                status = "Acceptable"
            elif empty_percentage < 20:
                status = "Poor"
            else:
                status = "Critical"
            
            field_stats[field] = {
                'Description': field,
                'Missing Count': empty_count,
                'Missing %': round(empty_percentage, 2),
                'Status': status
            }
    
    # Convert statistics to DataFrame
    stats_df = pd.DataFrame.from_dict(field_stats, orient='index')
    stats_df = stats_df.sort_values('Missing %')
    
    # Save to Excel
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        # General statistics
        pd.DataFrame([stats]).to_excel(writer, sheet_name='General Stats', index=False)
        
        # Field-based statistics
        stats_df.to_excel(writer, sheet_name='Field Stats')
        
        # Lost record analysis
        if 'Lost Records Analysis' in stats:
            pd.DataFrame(stats['Lost Records Analysis']).to_excel(writer, sheet_name='Lost Records')
    
    return field_stats 