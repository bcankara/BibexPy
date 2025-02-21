import os
import sys
import time
import threading
import pandas as pd
from dotenv import load_dotenv
from Main.modules import (
    ensure_dir,
    find_files,
    find_data_folders,
    save_statistics,
    save_comprehensive_statistics,
    merge_databases_simple,
    merge_databases_enhanced,
    compare_merge_methods,
    create_result_folder
)
from datetime import datetime
from colorama import Fore, Style

class ProcessIndicator:
    """Process indicator for showing progress"""
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
                sys.stdout.write(f"\r{self.description:<50}")
                self.visible = True
            sys.stdout.write(next(self.generator))
            sys.stdout.flush()
            sys.stdout.write('\b')
    
    def remove_indicator(self, cleanup=False):
        with self._screen_lock:
            if self.visible:
                sys.stdout.write('\b')
                self.visible = False
                if cleanup:
                    sys.stdout.write(f"\r{self.description:<50}[Completed]\n")
                    sys.stdout.flush()
    
    def indicator_task(self):
        while self.busy:
            self.write_next()
            time.sleep(self.delay)
    
    def __enter__(self):
        if sys.stdout.isatty():
            self._screen_lock = threading.Lock()
            self.busy = True
            self.thread = threading.Thread(target=self.indicator_task)
            self.thread.start()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if sys.stdout.isatty():
            self.busy = False
            time.sleep(self.delay)
            self.remove_indicator(cleanup=True)

def process_wos_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process WoS data"""
    try:
        from Main.wos2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting WoS data to Excel format") as indicator:
                df = pd.read_excel(output_file)
                stats = {
                    'Record Count': len(df),
                    'Column Count': len(df.columns),
                    'Non-Empty Columns': df.count().to_dict()
                }
                return True, df, stats
        return False, None, {}
    except Exception as e:
        sys.stderr.write(f"\nError: {str(e)}\n")
        return False, None, {}

def process_scopus_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process Scopus data"""
    try:
        from Main.scp2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting Scopus data to Excel format") as indicator:
                df = pd.read_excel(output_file)
                stats = {
                    'Record Count': len(df),
                    'Column Count': len(df.columns),
                    'Non-Empty Columns': df.count().to_dict()
                }
                return True, df, stats
        return False, None, {}
    except Exception as e:
        sys.stderr.write(f"\nError: {str(e)}\n")
        return False, None, {}

def merge_txt_files(data_dir: str) -> str:
    """Merge all txt files in data directory into wos_raw.txt in merged_raw folder"""
    txt_files = find_files(data_dir, "txt")
    if not txt_files:
        raise ValueError("No WoS files (txt) found in Data folder.")
    
    # Create merged_raw directory if not exists
    merged_raw_dir = os.path.join(data_dir, "merged_raw")
    os.makedirs(merged_raw_dir, exist_ok=True)
    
    output_file = os.path.join(merged_raw_dir, "wos_raw.txt")
    with open(output_file, 'w', encoding='utf-8') as outfile:
        for txt_file in txt_files:
            with open(txt_file, 'r', encoding='utf-8') as infile:
                outfile.write(infile.read())
                outfile.write('\n')  # Add newline between files
    
    return output_file

def merge_csv_files(data_dir: str) -> str:
    """Merge all csv files in data directory into scp_raw.csv in merged_raw folder"""
    csv_files = find_files(data_dir, "csv")
    if not csv_files:
        raise ValueError("No Scopus files (csv) found in Data folder.")
    
    # Create merged_raw directory if not exists
    merged_raw_dir = os.path.join(data_dir, "merged_raw")
    os.makedirs(merged_raw_dir, exist_ok=True)
    
    output_file = os.path.join(merged_raw_dir, "scp_raw.csv")
    
    if len(csv_files) == 1:
        # If only one CSV file, just copy it
        import shutil
        shutil.copy2(csv_files[0], output_file)
    else:
        # Read and combine all CSV files
        all_data = []
        for csv_file in csv_files:
            df = pd.read_csv(csv_file, encoding='utf-8')
            all_data.append(df)
        
        # Concatenate all dataframes
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # Save combined data
        combined_df.to_csv(output_file, index=False, encoding='utf-8')
    
    return output_file

def main():
    try:
        # Load environment variables
        load_dotenv()
        
        print("Database Merge Tool")
        print("------------------")
        
        # Find project folders
        workspace_dir = "Workspace"
        if not os.path.exists(workspace_dir):
            os.makedirs(workspace_dir)
        
        project_folders = [d for d in os.listdir(workspace_dir) if os.path.isdir(os.path.join(workspace_dir, d))]
        
        if not project_folders:
            sys.stderr.write("\nError: No project folder found in Workspace.\n")
            return
        
        print("\nProject Folders:")
        for i, folder in enumerate(project_folders, 1):
            print(f"{i}. {folder}")
        
        # Project selection
        while True:
            try:
                choice = int(input("\nSelect project number: "))
                if 1 <= choice <= len(project_folders):
                    project_dir = os.path.join(workspace_dir, project_folders[choice-1])
                    break
                else:
                    sys.stderr.write("Error: Invalid selection.\n")
            except ValueError:
                sys.stderr.write("Error: Invalid input. Please enter a number.\n")
        
        # Create Data directory if not exists
        data_dir = os.path.join(project_dir, "Data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        
        print(f"\nSelected Project: {project_folders[choice-1]}")
        
        # Create unique analysis directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        analysis_dir = os.path.join(project_dir, f"Analysis_{timestamp}")
        os.makedirs(analysis_dir, exist_ok=True)
        
        # Create subfolders
        text_files_dir = os.path.join(analysis_dir, "Text_Files")
        cell_files_dir = os.path.join(analysis_dir, "Cell_Files")
        os.makedirs(text_files_dir, exist_ok=True)
        os.makedirs(cell_files_dir, exist_ok=True)
        
        # Set output files in analysis directory
        wos_output = os.path.join(cell_files_dir, "WoS.xlsx")
        scopus_output = os.path.join(cell_files_dir, "Scopus.xlsx")
        merged_bib = os.path.join(cell_files_dir, "Merged_Bib.xlsx")
        merged_vos = os.path.join(text_files_dir, "Merged_Vos.txt")
        api_enriched_bib = os.path.join(cell_files_dir, "Merged_API_Enriched_Bib.xlsx")
        ml_enriched_bib = os.path.join(cell_files_dir, "Merged_ML_Enriched_Bib.xlsx")
        api_enriched_vos = os.path.join(text_files_dir, "Merged_API_Enriched_Vos.txt")
        ml_enriched_vos = os.path.join(text_files_dir, "Merged_ML_Enriched_Vos.txt")
        stats_excel = os.path.join(analysis_dir, "Statistics.xlsx")
        api_log = os.path.join(analysis_dir, "Api_Log.txt")
        api_updates = os.path.join(analysis_dir, "Api_Update.xlsx")
        ml_updates = os.path.join(analysis_dir, "ML_Update.xlsx")
        
        # Check if merged_raw directory exists and contains required files
        merged_raw_dir = os.path.join(data_dir, "merged_raw")
        wos_raw = os.path.join(merged_raw_dir, "wos_raw.txt")
        scp_raw = os.path.join(merged_raw_dir, "scp_raw.csv")
        
        use_existing = False
        if os.path.exists(merged_raw_dir) and os.path.exists(wos_raw) and os.path.exists(scp_raw):
            while True:
                choice = input("\nPreviously merged raw files found. What would you like to do?\n"
                             "1. Use previously merged files\n"
                             "2. Perform new merge\n"
                             "Your choice (1/2): ")
                if choice in ['1', '2']:
                    use_existing = (choice == '1')
                    break
                else:
                    sys.stderr.write("Invalid choice. Please enter 1 or 2.\n")
        
        if not use_existing:
            if os.path.exists(merged_raw_dir):
                print("\nDeleting old merged files...")
                import shutil
                shutil.rmtree(merged_raw_dir)
            
            # Merge txt files into wos_raw.txt
            print("\nMerging process in progress...")
            try:
                wos_input = merge_txt_files(data_dir)
                print(f"WoS files merged: {os.path.basename(wos_input)}")
            except ValueError as e:
                sys.stderr.write(f"\nError: {str(e)}\n")
                return
            
            # Merge csv files into scp_raw.csv
            print("\nMerging Scopus files...")
            try:
                scopus_input = merge_csv_files(data_dir)
                print(f"Scopus files merged: {os.path.basename(scopus_input)}")
            except ValueError as e:
                sys.stderr.write(f"\nError: {str(e)}\n")
                return
        else:
            print("\nUsing existing merged files...")
            wos_input = wos_raw
            scopus_input = scp_raw
        
        # Process WoS data
        print("\nProcessing WoS data...")
        wos_success, wos_df, wos_stats = process_wos_data(wos_input, wos_output)
        if not wos_success:
            sys.stderr.write("\nError: Failed to process WoS data.\n")
            return
        print("WoS data processing completed.")
        
        # Process Scopus data
        print("\nProcessing Scopus data...")
        scopus_success, scopus_df, scopus_stats = process_scopus_data(scopus_input, scopus_output)
        if not scopus_success:
            sys.stderr.write("\nError: Failed to process Scopus data.\n")
            return
        print("Scopus data processing completed.")
        
        # Enhanced merge
        print("\nMerging process in progress...")
        enhanced_success, enhanced_stats, enhanced_df = merge_databases_enhanced(
            wos_df, 
            scopus_df, 
            merged_bib,
            result_dir=analysis_dir
        )

        print("\nConverting data to VosViewer format...")
        from Main.xlsx2vos import convert_excel_to_wos
        convert_excel_to_wos(merged_bib, merged_vos)
        print("VosViewer conversion completed.")
        
        if enhanced_success:
            # Ask user for enrichment preference
            while True:
                print("\nEnrichment Options:")
                print("1. API Enrichment")
                print("2. ML Enrichment (Experimental)")
                print("3. API + ML Enrichment")
                print("4. No Enrichment")
                choice = input("Select enrichment method (1/2/3/4): ")
                
                if choice == "1":
                    # API Enrichment
                    print("\nPerforming API enrichment...")
                    from Main.modules.api_utils import extract_metadata
                    
                    # Read the merged file
                    merged_df = pd.read_excel(merged_bib)
                    
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
                    total_dois = len(dois_with_empty_fields)
                    print(f"\n{Fore.GREEN}Found {total_dois} DOIs with empty fields that can be enriched{Style.RESET_ALL}")
                    
                    # Ask for confirmation
                    while True:
                        confirm = input(f"\n{Fore.YELLOW}Do you want to proceed with API enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                        if confirm in ['Y', 'N']:
                            break
                        print("Invalid input. Please enter Y or N.")
                    
                    if confirm == 'Y':
                        # Perform API enrichment
                        enriched_df = merged_df.copy()
                        total_enriched = 0
                        
                        # Only process DOIs that have empty fields
                        dois_to_process = list(dois_with_empty_fields)
                        
                        for idx, row in merged_df.iterrows():
                            if pd.notna(row.get('DI')) and row['DI'] in dois_to_process:
                                doi = row['DI']
                                db_source = row['DB']
                                
                                print(f"\n{Fore.CYAN}Processing DOI ({total_enriched + 1}/{total_dois}): {Fore.WHITE}{doi}{Style.RESET_ALL}")
                                print(f"{Fore.YELLOW}Source Database: {db_source}{Style.RESET_ALL}")
                                
                                # Show empty fields before enrichment
                                empty_fields = []
                                for field_code, field_info in api_supported_fields.items():
                                    if field_code in merged_df.columns:
                                        current_value = row[field_code]
                                        if pd.isna(current_value) or str(current_value).strip() == '':
                                            empty_fields.append(f"{field_info['name']} ({field_code}) - Supported by: {', '.join(field_info['apis'])}")
                                
                                if empty_fields:
                                    print(f"\n{Fore.YELLOW}Empty Fields:{Style.RESET_ALL}")
                                    for field in empty_fields:
                                        print(f"  - {field}")
                                
                                # Perform API enrichment
                                print(f"\n{Fore.CYAN}Querying APIs...{Style.RESET_ALL}")
                                enriched_metadata = extract_metadata(doi, row.to_dict())
                                
                                # Show API sources used
                                if 'API_Sources' in enriched_metadata:
                                    print(f"\n{Fore.GREEN}API Sources Used:{Style.RESET_ALL}")
                                    for field, source in enriched_metadata['API_Sources'].items():
                                        if field in api_supported_fields:
                                            print(f"  - {api_supported_fields[field]['name']} ({field}): {source}")
                                
                                # Update and track changes
                                updated_fields = []
                                for field_code, field_info in api_supported_fields.items():
                                    if field_code in merged_df.columns:
                                        current_value = row[field_code]
                                        new_value = enriched_metadata.get(field_code)
                                        
                                        if (pd.isna(current_value) or str(current_value).strip() == '') and pd.notna(new_value) and str(new_value).strip() != '':
                                            enriched_df.loc[idx, field_code] = new_value
                                            api_source = enriched_metadata.get('API_Sources', {}).get(field_code, 'Unknown API')
                                            updated_fields.append(f"{field_info['name']} ({field_code}) from {api_source}")
                                
                                if updated_fields:
                                    total_enriched += 1
                                    print(f"\n{Fore.GREEN}Updated Fields:{Style.RESET_ALL}")
                                    for field in updated_fields:
                                        print(f"  + {field}")
                                else:
                                    print(f"\n{Fore.YELLOW}No fields were updated{Style.RESET_ALL}")
                                
                                print("-" * 50)
                        
                        # Show final enrichment statistics
                        print(f"\n{Fore.CYAN}Field Enrichment Summary:{Style.RESET_ALL}")
                        print("-" * 100)
                        print(f"{Fore.WHITE}Field Code | Field Name | Supporting APIs | Initial Empty | Final Empty | Improvement{Style.RESET_ALL}")
                        print("-" * 100)
                        
                        for field_code, field_info in api_supported_fields.items():
                            if field_code in merged_df.columns:
                                initial_empty = empty_stats[field_code]['Initial Empty']
                                initial_percentage = empty_stats[field_code]['Initial %']
                                
                                final_empty_mask = (enriched_df[field_code].isna()) | (enriched_df[field_code].astype(str).str.strip() == '')
                                final_empty = final_empty_mask.sum()
                                final_percentage = (final_empty / len(enriched_df)) * 100
                                
                                improvement = initial_empty - final_empty
                                improvement_rate = ((initial_empty - final_empty) / initial_empty * 100) if initial_empty > 0 else 0
                                
                                print(f"{field_code:^10} | {field_info['name']:<30} | {', '.join(field_info['apis']):<50} | {initial_empty:>5} ({initial_percentage:.2f}%) | {final_empty:>5} ({final_percentage:.2f}%) | {improvement:>5} ({improvement_rate:.2f}%)")
                        
                        print("-" * 100)
                        print(f"\n{Fore.GREEN}API enrichment completed. {total_enriched} records updated.{Style.RESET_ALL}")
                        
                        # Save API enriched results
                        enriched_df.to_excel(api_enriched_bib, index=False)
                        convert_excel_to_wos(api_enriched_bib, api_enriched_vos)
                        
                        # Save API updates to Excel
                        api_updates_df = pd.DataFrame(columns=['DOI', 'Field', 'Original Value', 'New Value', 'API Source'])
                        for idx, row in merged_df.iterrows():
                            doi = row['DI']
                            for field_code, field_info in api_supported_fields.items():
                                if field_code in merged_df.columns:
                                    original_value = row[field_code]
                                    new_value = enriched_df.loc[idx, field_code]
                                    
                                    if pd.notna(new_value) and (pd.isna(original_value) or str(original_value).strip() == ''):
                                        api_source = enriched_metadata.get('API_Sources', {}).get(field_code, 'Unknown API')
                                        api_updates_df = pd.concat([api_updates_df, pd.DataFrame({
                                            'DOI': [doi],
                                            'Field': [f"{field_info['name']} ({field_code})"],
                                            'Original Value': [original_value],
                                            'New Value': [new_value],
                                            'API Source': [api_source]
                                        })], ignore_index=True)
                        
                        api_updates_df.to_excel(api_updates, index=False)
                        
                        # Save API enrichment statistics
                        stats_df = pd.DataFrame(columns=['Field Code', 'Field Name', 'Initial Empty', 'Final Empty', 'Improvement', 'Improvement Rate', 'Supporting APIs'])
                        
                        for field_code, field_info in api_supported_fields.items():
                            if field_code in merged_df.columns:
                                initial_empty = empty_stats[field_code]['Initial Empty']
                                initial_percentage = empty_stats[field_code]['Initial %']
                                
                                final_empty_mask = (enriched_df[field_code].isna()) | (enriched_df[field_code].astype(str).str.strip() == '')
                                final_empty = final_empty_mask.sum()
                                final_percentage = (final_empty / len(enriched_df)) * 100
                                
                                improvement = initial_empty - final_empty
                                improvement_rate = ((initial_empty - final_empty) / initial_empty * 100) if initial_empty > 0 else 0
                                
                                stats_df = pd.concat([stats_df, pd.DataFrame({
                                    'Field Code': [field_code],
                                    'Field Name': [field_info['name']],
                                    'Initial Empty': [initial_empty],
                                    'Initial %': [initial_percentage],
                                    'Final Empty': [final_empty],
                                    'Final %': [final_percentage],
                                    'Improvement': [improvement],
                                    'Improvement Rate': [f"{improvement_rate:.2f}%"],
                                    'Supporting APIs': [', '.join(field_info['apis'])]
                                })], ignore_index=True)
                        
                        # Save comprehensive statistics for enriched data
                        enriched_stats = {
                            'WoS Statistics': wos_stats,
                            'Scopus Statistics': scopus_stats,
                            'Merge Statistics': enhanced_stats,
                            'API Enrichment Statistics': {
                                'Total Records': len(enriched_df),
                                'Enriched Records': total_enriched,
                                'Field Statistics': stats_df.to_dict('records')
                            }
                        }
                        
                        # Save statistics to Excel
                        with pd.ExcelWriter(stats_excel, engine='openpyxl') as writer:
                            # Original statistics
                            pd.DataFrame([wos_stats]).to_excel(writer, sheet_name='WoS Stats', index=False)
                            pd.DataFrame([scopus_stats]).to_excel(writer, sheet_name='Scopus Stats', index=False)
                            pd.DataFrame([enhanced_stats]).to_excel(writer, sheet_name='Merge Stats', index=False)
                            
                            # API enrichment statistics
                            stats_df.to_excel(writer, sheet_name='API Enrichment Stats', index=False)
                            api_updates_df.to_excel(writer, sheet_name='API Updates', index=False)
                        
                        # Save API log
                        with open(api_log, 'w', encoding='utf-8') as f:
                            f.write("API Enrichment Log\n")
                            f.write("=================\n\n")
                            f.write(f"Total Records: {len(enriched_df)}\n")
                            f.write(f"Enriched Records: {total_enriched}\n\n")
                            f.write("Field Enrichment Summary:\n")
                            f.write("----------------------\n")
                            for _, row in stats_df.iterrows():
                                f.write(f"\nField: {row['Field Name']} ({row['Field Code']})\n")
                                f.write(f"Initial Empty: {row['Initial Empty']} ({row['Initial %']:.2f}%)\n")
                                f.write(f"Final Empty: {row['Final Empty']} ({row['Final %']:.2f}%)\n")
                                f.write(f"Improvement: {row['Improvement']} records ({row['Improvement Rate']})\n")
                                f.write(f"Supporting APIs: {row['Supporting APIs']}\n")
                                f.write("-" * 50 + "\n")
                        
                        # After API enrichment is complete, ask about ML enrichment
                        while True:
                            ml_confirm = input(f"\n{Fore.YELLOW}Would you like to perform ML enrichment after API enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                            if ml_confirm in ['Y', 'N']:
                                break
                            print("Invalid input. Please enter Y or N.")
                        
                        if ml_confirm == 'Y':
                            # Proceed with ML enrichment using original merged file
                            print(f"\nPerforming ML enrichment using original merged data...")
                            merged_df = pd.read_excel(merged_bib)  # Always use original data
                            
                            # Show ML enrichment capabilities
                            print(f"\n{Fore.CYAN}ML Enrichment Capabilities:{Style.RESET_ALL}")
                            print("-" * 50)
                            print(f"{Fore.WHITE}Field | Training Data | Empty Records | Model Type{Style.RESET_ALL}")
                            print("-" * 50)
                            
                            # Check training data availability
                            de_train = len(merged_df[merged_df['DE'].notna()])
                            id_train = len(merged_df[merged_df['ID'].notna()])  # Add ID training data count
                            sc_train = len(merged_df[merged_df['SC'].notna()])
                            de_empty = merged_df['DE'].isna().sum()
                            id_empty = merged_df['ID'].isna().sum()  # Add ID empty count
                            sc_empty = merged_df['SC'].isna().sum()
                            
                            # Show DE stats with color coding
                            if de_train > 100:
                                train_color = Fore.GREEN
                            elif de_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if de_empty == 0:
                                empty_color = Fore.GREEN
                            elif de_empty < len(merged_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}DE  | {train_color}{de_train:>5} records{Style.RESET_ALL} | {empty_color}{de_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            
                            # Show ID stats with color coding
                            if id_train > 100:
                                train_color = Fore.GREEN
                            elif id_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if id_empty == 0:
                                empty_color = Fore.GREEN
                            elif id_empty < len(merged_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}ID  | {train_color}{id_train:>5} records{Style.RESET_ALL} | {empty_color}{id_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            
                            # Show SC stats with color coding
                            if sc_train > 100:
                                train_color = Fore.GREEN
                            elif sc_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if sc_empty == 0:
                                empty_color = Fore.GREEN
                            elif sc_empty < len(merged_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}SC  | {train_color}{sc_train:>5} records{Style.RESET_ALL} | {empty_color}{sc_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            print("-" * 50)
                            
                            # Show warning if training data is insufficient
                            if de_train < 10 and id_train < 10 and sc_train < 10:  # Updated condition to include ID
                                print(f"\n{Fore.RED}Warning: Insufficient training data for all fields.{Style.RESET_ALL}")
                                print("ML enrichment requires at least 10 records with known values.")
                                print("Consider using API enrichment instead.")
                                continue
                            
                            # Ask for confirmation
                            while True:
                                ml_confirm = input(f"\n{Fore.YELLOW}Do you want to proceed with ML enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                                if ml_confirm in ['Y', 'N']:
                                    break
                                print("Invalid input. Please enter Y or N.")
                            
                            if ml_confirm == 'Y':
                                try:
                                    # Perform ML enrichment with progress indicator
                                    with ProcessIndicator("Training and applying ML models") as indicator:
                                        enriched_df, ml_stats = enrich_metadata_ml(merged_df)
                                    
                                    # Save ML enriched results
                                    enriched_df.to_excel(ml_enriched_bib, index=False)
                                    convert_excel_to_wos(ml_enriched_bib, ml_enriched_vos)
                                    
                                    # Create ML statistics DataFrame
                                    ml_stats_df = pd.DataFrame([{
                                        'Metric': 'Total Records',
                                        'Value': ml_stats['total_records']
                                    }, {
                                        'Metric': 'Original Empty Keywords (DE)',
                                        'Value': ml_stats['original_empty_keywords']
                                    }, {
                                        'Metric': 'Original Empty Keywords Plus (ID)',
                                        'Value': ml_stats['original_empty_id']
                                    }, {
                                        'Metric': 'Original Empty Subjects (SC)',
                                        'Value': ml_stats['original_empty_subjects']
                                    }, {
                                        'Metric': 'Remaining Empty Keywords (DE)',
                                        'Value': ml_stats['enriched_empty_keywords']
                                    }, {
                                        'Metric': 'Remaining Empty Keywords Plus (ID)',
                                        'Value': ml_stats['enriched_empty_id']
                                    }, {
                                        'Metric': 'Remaining Empty Subjects (SC)',
                                        'Value': ml_stats['enriched_empty_subjects']
                                    }, {
                                        'Metric': 'Keywords (DE) Filled',
                                        'Value': ml_stats['keywords_filled']
                                    }, {
                                        'Metric': 'Keywords Plus (ID) Filled',
                                        'Value': ml_stats['id_filled']
                                    }, {
                                        'Metric': 'Subjects (SC) Filled',
                                        'Value': ml_stats['subjects_filled']
                                    }, {
                                        'Metric': 'Keywords Fill Rate (%)',
                                        'Value': round((ml_stats['keywords_filled'] / ml_stats['original_empty_keywords'] * 100), 2) if ml_stats['original_empty_keywords'] > 0 else 0
                                    }, {
                                        'Metric': 'Keywords Plus Fill Rate (%)',
                                        'Value': round((ml_stats['id_filled'] / ml_stats['original_empty_id'] * 100), 2) if ml_stats['original_empty_id'] > 0 else 0
                                    }, {
                                        'Metric': 'Subjects Fill Rate (%)',
                                        'Value': round((ml_stats['subjects_filled'] / ml_stats['original_empty_subjects'] * 100), 2) if ml_stats['original_empty_subjects'] > 0 else 0
                                    }])
                                    
                                    # Save ML statistics to Excel
                                    ml_stats_df.to_excel(ml_updates, index=False)
                                    
                                    # Show enrichment results
                                    print(f"\n{Fore.CYAN}ML Enrichment Results:{Style.RESET_ALL}")
                                    print(f"Keywords (DE) filled: {Fore.GREEN}{ml_stats['keywords_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_keywords']}")
                                    print(f"Keywords Plus (ID) filled: {Fore.GREEN}{ml_stats['id_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_id']}")
                                    print(f"Subject Categories (SC) filled: {Fore.GREEN}{ml_stats['subjects_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_subjects']}")
                                    
                                    # After ML enrichment is complete, ask about API enrichment
                                    while True:
                                        api_confirm = input(f"\n{Fore.YELLOW}Would you like to perform API enrichment after ML enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                                        if api_confirm in ['Y', 'N']:
                                            break
                                        print("Invalid input. Please enter Y or N.")
                                    
                                    if api_confirm == 'Y':
                                        # Proceed with API enrichment using original merged file
                                        print(f"\nPerforming API enrichment using original merged data...")
                                        merged_df = pd.read_excel(merged_bib)  # Her zaman orijinal dosyayı kullan
                                        
                                        # API enrichment code
                                        from Main.modules.api_utils import extract_metadata
                                        from Main.modules.merge_utils import enrich_with_api
                                        
                                        # Perform API enrichment
                                        api_success, api_stats, enriched_df = enrich_with_api(
                                            merged_df,
                                            api_log=api_log,
                                            api_updates=api_updates,
                                            result_dir=analysis_dir
                                        )
                                        
                                        if api_success:
                                            print("\nAPI enrichment completed successfully.")
                                        else:
                                            print("\nAPI enrichment process encountered some issues.")
                                        
                                        # Save comprehensive statistics
                                        all_stats = {
                                            'WoS Statistics': wos_stats,
                                            'Scopus Statistics': scopus_stats,
                                            'Merge Statistics': enhanced_stats,
                                            'ML Statistics': ml_stats,
                                            'API Statistics': api_stats if api_success else {}
                                        }
                                        
                                        save_comprehensive_statistics(all_stats, wos_df, scopus_df, enriched_df, stats_excel)
                                        print("Statistics completed.")
                                except Exception as e:
                                    print(f"\nError during ML enrichment: {str(e)}")
                                    print("Please try API enrichment instead.")
                            else:
                                print("\nML enrichment cancelled.")
                            break
                    else:
                        print("\nAPI enrichment cancelled.")
                    break
                    
                elif choice == "2":
                    # ML Enrichment
                    print("\nPerforming ML enrichment (Experimental)...")
                    from Main.modules.ml_utils import enrich_metadata_ml
                    
                    # Read the merged file
                    merged_df = pd.read_excel(merged_bib)
                    
                    # Show ML enrichment capabilities
                    print(f"\n{Fore.CYAN}ML Enrichment Capabilities:{Style.RESET_ALL}")
                    print("-" * 50)
                    print(f"{Fore.WHITE}Field | Training Data | Empty Records | Model Type{Style.RESET_ALL}")
                    print("-" * 50)
                    
                    # Check training data availability
                    de_train = len(merged_df[merged_df['DE'].notna()])
                    id_train = len(merged_df[merged_df['ID'].notna()])  # Add ID training data count
                    sc_train = len(merged_df[merged_df['SC'].notna()])
                    de_empty = merged_df['DE'].isna().sum()
                    id_empty = merged_df['ID'].isna().sum()  # Add ID empty count
                    sc_empty = merged_df['SC'].isna().sum()
                    
                    # Show DE stats with color coding
                    if de_train > 100:
                        train_color = Fore.GREEN
                    elif de_train > 50:
                        train_color = Fore.YELLOW
                    else:
                        train_color = Fore.RED
                    
                    if de_empty == 0:
                        empty_color = Fore.GREEN
                    elif de_empty < len(merged_df) * 0.3:
                        empty_color = Fore.YELLOW
                    else:
                        empty_color = Fore.RED
                    
                    print(f"{Fore.WHITE}DE  | {train_color}{de_train:>5} records{Style.RESET_ALL} | {empty_color}{de_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                    
                    # Show ID stats with color coding
                    if id_train > 100:
                        train_color = Fore.GREEN
                    elif id_train > 50:
                        train_color = Fore.YELLOW
                    else:
                        train_color = Fore.RED
                    
                    if id_empty == 0:
                        empty_color = Fore.GREEN
                    elif id_empty < len(merged_df) * 0.3:
                        empty_color = Fore.YELLOW
                    else:
                        empty_color = Fore.RED
                    
                    print(f"{Fore.WHITE}ID  | {train_color}{id_train:>5} records{Style.RESET_ALL} | {empty_color}{id_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                    
                    # Show SC stats with color coding
                    if sc_train > 100:
                        train_color = Fore.GREEN
                    elif sc_train > 50:
                        train_color = Fore.YELLOW
                    else:
                        train_color = Fore.RED
                    
                    if sc_empty == 0:
                        empty_color = Fore.GREEN
                    elif sc_empty < len(merged_df) * 0.3:
                        empty_color = Fore.YELLOW
                    else:
                        empty_color = Fore.RED
                    
                    print(f"{Fore.WHITE}SC  | {train_color}{sc_train:>5} records{Style.RESET_ALL} | {empty_color}{sc_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                    print("-" * 50)
                    
                    # Show warning if training data is insufficient
                    if de_train < 10 and id_train < 10 and sc_train < 10:  # Updated condition to include ID
                        print(f"\n{Fore.RED}Warning: Insufficient training data for all fields.{Style.RESET_ALL}")
                        print("ML enrichment requires at least 10 records with known values.")
                        print("Consider using API enrichment instead.")
                        continue
                    
                    # Ask for confirmation
                    while True:
                        confirm = input(f"\n{Fore.YELLOW}Do you want to proceed with ML enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                        if confirm in ['Y', 'N']:
                            break
                        print("Invalid input. Please enter Y or N.")
                    
                    if confirm == 'Y':
                        try:
                            # Perform ML enrichment with progress indicator
                            with ProcessIndicator("Training and applying ML models") as indicator:
                                enriched_df, ml_stats = enrich_metadata_ml(merged_df)
                            
                            # Save ML enriched results
                            enriched_df.to_excel(ml_enriched_bib, index=False)
                            convert_excel_to_wos(ml_enriched_bib, ml_enriched_vos)
                            
                            # Create ML statistics DataFrame
                            ml_stats_df = pd.DataFrame([{
                                'Metric': 'Total Records',
                                'Value': ml_stats['total_records']
                            }, {
                                'Metric': 'Original Empty Keywords (DE)',
                                'Value': ml_stats['original_empty_keywords']
                            }, {
                                'Metric': 'Original Empty Keywords Plus (ID)',
                                'Value': ml_stats['original_empty_id']
                            }, {
                                'Metric': 'Original Empty Subjects (SC)',
                                'Value': ml_stats['original_empty_subjects']
                            }, {
                                'Metric': 'Remaining Empty Keywords (DE)',
                                'Value': ml_stats['enriched_empty_keywords']
                            }, {
                                'Metric': 'Remaining Empty Keywords Plus (ID)',
                                'Value': ml_stats['enriched_empty_id']
                            }, {
                                'Metric': 'Remaining Empty Subjects (SC)',
                                'Value': ml_stats['enriched_empty_subjects']
                            }, {
                                'Metric': 'Keywords (DE) Filled',
                                'Value': ml_stats['keywords_filled']
                            }, {
                                'Metric': 'Keywords Plus (ID) Filled',
                                'Value': ml_stats['id_filled']
                            }, {
                                'Metric': 'Subjects (SC) Filled',
                                'Value': ml_stats['subjects_filled']
                            }, {
                                'Metric': 'Keywords Fill Rate (%)',
                                'Value': round((ml_stats['keywords_filled'] / ml_stats['original_empty_keywords'] * 100), 2) if ml_stats['original_empty_keywords'] > 0 else 0
                            }, {
                                'Metric': 'Keywords Plus Fill Rate (%)',
                                'Value': round((ml_stats['id_filled'] / ml_stats['original_empty_id'] * 100), 2) if ml_stats['original_empty_id'] > 0 else 0
                            }, {
                                'Metric': 'Subjects Fill Rate (%)',
                                'Value': round((ml_stats['subjects_filled'] / ml_stats['original_empty_subjects'] * 100), 2) if ml_stats['original_empty_subjects'] > 0 else 0
                            }])
                            
                            # Save ML statistics to Excel
                            ml_stats_df.to_excel(ml_updates, index=False)
                            
                            # Show enrichment results
                            print(f"\n{Fore.CYAN}ML Enrichment Results:{Style.RESET_ALL}")
                            print(f"Keywords (DE) filled: {Fore.GREEN}{ml_stats['keywords_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_keywords']}")
                            print(f"Keywords Plus (ID) filled: {Fore.GREEN}{ml_stats['id_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_id']}")
                            print(f"Subject Categories (SC) filled: {Fore.GREEN}{ml_stats['subjects_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_subjects']}")
                            
                            # After ML enrichment is complete, ask about API enrichment
                            while True:
                                api_confirm = input(f"\n{Fore.YELLOW}Would you like to perform API enrichment after ML enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                                if api_confirm in ['Y', 'N']:
                                    break
                                print("Invalid input. Please enter Y or N.")
                            
                            if api_confirm == 'Y':
                                # Proceed with API enrichment using original merged file
                                print(f"\nPerforming API enrichment using original merged data...")
                                merged_df = pd.read_excel(merged_bib)  # Her zaman orijinal dosyayı kullan
                                
                                # API enrichment code
                                from Main.modules.api_utils import extract_metadata
                                from Main.modules.merge_utils import enrich_with_api
                                
                                # Perform API enrichment
                                api_success, api_stats, enriched_df = enrich_with_api(
                                    merged_df,
                                    api_log=api_log,
                                    api_updates=api_updates,
                                    result_dir=analysis_dir
                                )
                                
                                if api_success:
                                    print("\nAPI enrichment completed successfully.")
                                else:
                                    print("\nAPI enrichment process encountered some issues.")
                                
                                # Save comprehensive statistics
                                all_stats = {
                                    'WoS Statistics': wos_stats,
                                    'Scopus Statistics': scopus_stats,
                                    'Merge Statistics': enhanced_stats,
                                    'ML Statistics': ml_stats,
                                    'API Statistics': api_stats if api_success else {}
                                }
                                
                                save_comprehensive_statistics(all_stats, wos_df, scopus_df, enriched_df, stats_excel)
                                print("Statistics completed.")
                        except Exception as e:
                            print(f"\nError during ML enrichment: {str(e)}")
                            print("Please try API enrichment instead.")
                    else:
                        print("\nML enrichment cancelled.")
                    break
                    
                elif choice == "3":
                    # Combined API + ML Enrichment
                    print("\nPerforming API enrichment followed by ML enrichment...")
                    
                    # First perform API enrichment using existing logic
                    from Main.modules.api_utils import extract_metadata
                    from Main.modules.merge_utils import enrich_with_api
                    
                    # Read the merged file
                    merged_df = pd.read_excel(merged_bib)
                    
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
                    total_dois = len(dois_with_empty_fields)
                    print(f"\n{Fore.GREEN}Found {total_dois} DOIs with empty fields that can be enriched{Style.RESET_ALL}")
                    
                    # Ask for confirmation before API enrichment
                    while True:
                        api_confirm = input(f"\n{Fore.YELLOW}Do you want to proceed with API enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                        if api_confirm in ['Y', 'N']:
                            break
                        print("Invalid input. Please enter Y or N.")
                    
                    if api_confirm == 'Y':
                        # Perform API enrichment using the same function as option 1
                        api_success, api_stats, api_enriched_df = enrich_with_api(
                            merged_df,
                            api_log=api_log,
                            api_updates=api_updates,
                            result_dir=analysis_dir
                        )
                        
                        if api_success:
                            print("\nAPI enrichment completed successfully.")
                            
                            # Save temporary API enriched file
                            temp_api_file = os.path.join(cell_files_dir, 'temp_api_enriched.xlsx')
                            api_enriched_df.to_excel(temp_api_file, index=False)
                            
                            # Now perform ML enrichment using existing logic
                            print("\nPerforming ML enrichment on API-enriched data...")
                            from Main.modules.ml_utils import enrich_metadata_ml
                            
                            # Read the temporary API enriched file
                            api_enriched_df = pd.read_excel(temp_api_file)
                            
                            # Show ML enrichment capabilities
                            print(f"\n{Fore.CYAN}ML Enrichment Capabilities:{Style.RESET_ALL}")
                            print("-" * 50)
                            print(f"{Fore.WHITE}Field | Training Data | Empty Records | Model Type{Style.RESET_ALL}")
                            print("-" * 50)
                            
                            # Check training data availability
                            de_train = len(api_enriched_df[api_enriched_df['DE'].notna()])
                            id_train = len(api_enriched_df[api_enriched_df['ID'].notna()])  # Add ID training data count
                            sc_train = len(api_enriched_df[api_enriched_df['SC'].notna()])
                            de_empty = api_enriched_df['DE'].isna().sum()
                            id_empty = api_enriched_df['ID'].isna().sum()  # Add ID empty count
                            sc_empty = api_enriched_df['SC'].isna().sum()
                            
                            # Show DE stats with color coding
                            if de_train > 100:
                                train_color = Fore.GREEN
                            elif de_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if de_empty == 0:
                                empty_color = Fore.GREEN
                            elif de_empty < len(api_enriched_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}DE  | {train_color}{de_train:>5} records{Style.RESET_ALL} | {empty_color}{de_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            
                            # Show ID stats with color coding
                            if id_train > 100:
                                train_color = Fore.GREEN
                            elif id_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if id_empty == 0:
                                empty_color = Fore.GREEN
                            elif id_empty < len(api_enriched_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}ID  | {train_color}{id_train:>5} records{Style.RESET_ALL} | {empty_color}{id_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            
                            # Show SC stats with color coding
                            if sc_train > 100:
                                train_color = Fore.GREEN
                            elif sc_train > 50:
                                train_color = Fore.YELLOW
                            else:
                                train_color = Fore.RED
                            
                            if sc_empty == 0:
                                empty_color = Fore.GREEN
                            elif sc_empty < len(api_enriched_df) * 0.3:
                                empty_color = Fore.YELLOW
                            else:
                                empty_color = Fore.RED
                            
                            print(f"{Fore.WHITE}SC  | {train_color}{sc_train:>5} records{Style.RESET_ALL} | {empty_color}{sc_empty:>5} records{Style.RESET_ALL} | TF-IDF + RandomForest")
                            print("-" * 50)
                            
                            # Show warning if training data is insufficient
                            if de_train < 10 and id_train < 10 and sc_train < 10:  # Updated condition to include ID
                                print(f"\n{Fore.RED}Warning: Insufficient training data for all fields.{Style.RESET_ALL}")
                                print("ML enrichment requires at least 10 records with known values.")
                                print("Consider using API enrichment instead.")
                                continue
                            
                            # Ask for confirmation
                            while True:
                                confirm = input(f"\n{Fore.YELLOW}Do you want to proceed with ML enrichment? (Y/N): {Style.RESET_ALL}").strip().upper()
                                if confirm in ['Y', 'N']:
                                    break
                                print("Invalid input. Please enter Y or N.")
                            
                            if confirm == 'Y':
                                try:
                                    # Perform ML enrichment with progress indicator
                                    with ProcessIndicator("Training and applying ML models") as indicator:
                                        enriched_df, ml_stats = enrich_metadata_ml(api_enriched_df)
                                    
                                    # Save ML enriched results
                                    enriched_df.to_excel(ml_enriched_bib, index=False)
                                    convert_excel_to_wos(ml_enriched_bib, ml_enriched_vos)
                                    
                                    # Create ML statistics DataFrame
                                    ml_stats_df = pd.DataFrame([{
                                        'Metric': 'Total Records',
                                        'Value': ml_stats['total_records']
                                    }, {
                                        'Metric': 'Original Empty Keywords (DE)',
                                        'Value': ml_stats['original_empty_keywords']
                                    }, {
                                        'Metric': 'Original Empty Keywords Plus (ID)',
                                        'Value': ml_stats['original_empty_id']
                                    }, {
                                        'Metric': 'Original Empty Subjects (SC)',
                                        'Value': ml_stats['original_empty_subjects']
                                    }, {
                                        'Metric': 'Remaining Empty Keywords (DE)',
                                        'Value': ml_stats['enriched_empty_keywords']
                                    }, {
                                        'Metric': 'Remaining Empty Keywords Plus (ID)',
                                        'Value': ml_stats['enriched_empty_id']
                                    }, {
                                        'Metric': 'Remaining Empty Subjects (SC)',
                                        'Value': ml_stats['enriched_empty_subjects']
                                    }, {
                                        'Metric': 'Keywords (DE) Filled',
                                        'Value': ml_stats['keywords_filled']
                                    }, {
                                        'Metric': 'Keywords Plus (ID) Filled',
                                        'Value': ml_stats['id_filled']
                                    }, {
                                        'Metric': 'Subjects (SC) Filled',
                                        'Value': ml_stats['subjects_filled']
                                    }, {
                                        'Metric': 'Keywords Fill Rate (%)',
                                        'Value': round((ml_stats['keywords_filled'] / ml_stats['original_empty_keywords'] * 100), 2) if ml_stats['original_empty_keywords'] > 0 else 0
                                    }, {
                                        'Metric': 'Keywords Plus Fill Rate (%)',
                                        'Value': round((ml_stats['id_filled'] / ml_stats['original_empty_id'] * 100), 2) if ml_stats['original_empty_id'] > 0 else 0
                                    }, {
                                        'Metric': 'Subjects Fill Rate (%)',
                                        'Value': round((ml_stats['subjects_filled'] / ml_stats['original_empty_subjects'] * 100), 2) if ml_stats['original_empty_subjects'] > 0 else 0
                                    }])
                                    
                                    # Save ML statistics to Excel
                                    ml_stats_df.to_excel(ml_updates, index=False)
                                    
                                    # Show enrichment results
                                    print(f"\n{Fore.CYAN}ML Enrichment Results:{Style.RESET_ALL}")
                                    print(f"Keywords (DE) filled: {Fore.GREEN}{ml_stats['keywords_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_keywords']}")
                                    print(f"Keywords Plus (ID) filled: {Fore.GREEN}{ml_stats['id_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_id']}")
                                    print(f"Subject Categories (SC) filled: {Fore.GREEN}{ml_stats['subjects_filled']}{Style.RESET_ALL} out of {ml_stats['original_empty_subjects']}")
                                    
                                    # Save final results
                                    final_output_path = os.path.join(cell_files_dir, 'Merged_API_ML_Enrichment_Bib.xlsx')
                                    enriched_df.to_excel(final_output_path, index=False)
                                    
                                    # Convert to VosViewer format
                                    final_vos_path = os.path.join(text_files_dir, 'Merged_API_ML_Enrichment.txt')
                                    from Main.xlsx2vos import convert_excel_to_wos
                                    convert_excel_to_wos(final_output_path, final_vos_path)
                                    
                                    print(f"\nEnrichment completed. Files saved as:")
                                    print(f"Excel: {final_output_path}")
                                    print(f"VosViewer: {final_vos_path}")
                                    
                                    # Save comprehensive statistics
                                    all_stats = {
                                        'WoS Statistics': wos_stats,
                                        'Scopus Statistics': scopus_stats,
                                        'Merge Statistics': enhanced_stats,
                                        'API Statistics': api_stats,
                                        'ML Statistics': ml_stats
                                    }
                                    save_comprehensive_statistics(all_stats, wos_df, scopus_df, enriched_df, stats_excel)
                                    
                                    # Clean up temporary file
                                    if os.path.exists(temp_api_file):
                                        os.remove(temp_api_file)
                                    
                                except Exception as e:
                                    print(f"\nError during ML enrichment: {str(e)}")
                                    print("ML enrichment failed, but API enriched data is still available.")
                                    
                                    # Clean up temporary file
                                    if os.path.exists(temp_api_file):
                                        os.remove(temp_api_file)
                        else:
                            print("\nAPI enrichment process encountered issues. Combined enrichment cannot proceed.")
                        break
                    
                elif choice == "4":
                    print("\nSkipping enrichment...")
                    break
                else:
                    print("\nInvalid choice. Please select 1, 2, 3, or 4.")
            
            # Save statistics
            print("\nPreparing statistics...")
            all_stats = {
                'WoS Statistics': wos_stats,
                'Scopus Statistics': scopus_stats,
                'Merge Statistics': enhanced_stats
            }
            
            save_comprehensive_statistics(all_stats, wos_df, scopus_df, enhanced_df, stats_excel)
            print("Statistics completed.")
            
            print("\nProcess completed successfully.")
            print(f"\nAnalysis results saved to: {analysis_dir}")
            print("Generated Files:")
            print(f"1. WoS Data: {os.path.basename(wos_output)}")
            print(f"2. Scopus Data: {os.path.basename(scopus_output)}")
            print(f"3. Merged Data (Biblioshiny): {os.path.basename(merged_bib)}")
            print(f"4. Merged Data (VosViewer): {os.path.basename(merged_vos)}")
            print(f"5. Statistics: {os.path.basename(stats_excel)}")
            
            if os.path.exists(api_enriched_bib):
                print(f"6. API Enriched Data (Biblioshiny): {os.path.basename(api_enriched_bib)}")
                print(f"7. API Enriched Data (VosViewer): {os.path.basename(api_enriched_vos)}")
                print(f"8. API Log: {os.path.basename(api_log)}")
                if os.path.exists(api_updates):
                    print(f"9. API Updates: {os.path.basename(api_updates)}")
            
            if os.path.exists(ml_enriched_bib):
                print(f"10. ML Enriched Data (Biblioshiny): {os.path.basename(ml_enriched_bib)}")
                print(f"11. ML Enriched Data (VosViewer): {os.path.basename(ml_enriched_vos)}")
                print(f"12. ML Updates: {os.path.basename(ml_updates)}")
                
        else:
            sys.stderr.write("\nError: Merge process failed.\n")
    
    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}")
    finally:
        print("\nProgram terminating...")

if __name__ == "__main__":
    main() 