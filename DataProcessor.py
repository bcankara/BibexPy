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
        api_enriched_vos = os.path.join(text_files_dir, "Merged_API_Enriched_Vos.txt")
        stats_excel = os.path.join(analysis_dir, "Statistics.xlsx")
        api_log = os.path.join(analysis_dir, "Api_Log.txt")
        api_updates = os.path.join(analysis_dir, "Api_Update.xlsx")
        
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
        
        # Get Scopus API key from environment variable
        scopus_api_key = os.getenv('SCOPUS_API_KEY')
        unpaywall_email = os.getenv('UNPAYWALL_EMAIL')

        if not scopus_api_key:
            print("\nWarning: Scopus API key not found in .env file.")
            print("You can add it to the .env file to enable Scopus metadata enrichment.")
            print("Continuing with other data sources...")

        if not unpaywall_email:
            print("\nWarning: Unpaywall email not found in .env file.")
            print("You can add it to the .env file to enable Unpaywall metadata enrichment.")
            print("Continuing with other data sources...")
            
        # Enhanced merge
        print("\nMerging process in progress...")
        enhanced_success, enhanced_stats, enhanced_df = merge_databases_enhanced(
            wos_df, 
            scopus_df, 
            merged_bib, 
            scopus_api_key=scopus_api_key,
            unpaywall_email=unpaywall_email,
            result_dir=analysis_dir
        )

        print("\nConverting data to VosViewer format...")
        from Main.xlsx2vos import convert_excel_to_wos
        convert_excel_to_wos(merged_bib, merged_vos)
        print("VosViewer conversion completed.")
        
        if enhanced_success:
            # Save statistics
            print("\nPreparing statistics...")
            all_stats = {
                'WoS Statistics': wos_stats,
                'Scopus Statistics': scopus_stats,
                'Merge Statistics': enhanced_stats
            }
            
            save_comprehensive_statistics(all_stats, wos_df, scopus_df, enhanced_df, stats_excel)
            print("Statistics completed.")
            
            # Check and convert API enriched file
            if os.path.exists(api_enriched_bib):
                print("\nConverting API Enriched data to VosViewer format...")
                convert_excel_to_wos(api_enriched_bib, api_enriched_vos)
                print("API Enriched VosViewer conversion completed.")
            
            print("\nProcess completed successfully.")
            print(f"\nAnalysis results saved to: {analysis_dir}")
            print("Generated Files:")
            print(f"1. WoS Data: {os.path.basename(wos_output)}")
            print(f"2. Scopus Data: {os.path.basename(scopus_output)}")
            print(f"3. Merged Data (Biblioshiny): {os.path.basename(merged_bib)}")
            print(f"4. Merged Data (VosViewer): {os.path.basename(merged_vos)}")
            print(f"5. Statistics: {os.path.basename(stats_excel)}")
            print(f"6. API Log: {os.path.basename(api_log)}")
            if os.path.exists(api_updates):
                print(f"7. API Updates: {os.path.basename(api_updates)}")
            if os.path.exists(api_enriched_bib):
                print(f"8. API Enriched Data (Biblioshiny): {os.path.basename(api_enriched_bib)}")
                print(f"9. API Enriched Data (VosViewer): {os.path.basename(api_enriched_vos)}")
        else:
            sys.stderr.write("\nError: Merge process failed.\n")
    
    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}")
    finally:
        print("\nProgram terminating...")

if __name__ == "__main__":
    main() 