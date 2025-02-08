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

def process_pubmed_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process PubMed data"""
    try:
        from Main.pubmed2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting PubMed data to Excel format") as indicator:
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

def process_dimensions_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process Dimensions data"""
    try:
        from Main.dimensions2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting Dimensions data to Excel format") as indicator:
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

def process_cochrane_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process Cochrane data"""
    try:
        from Main.cochrane2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting Cochrane data to Excel format") as indicator:
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

def process_lens_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process Lens data"""
    try:
        from Main.lens2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting Lens data to Excel format") as indicator:
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

def process_openAlex_data(input_file: str, output_file: str) -> tuple[bool, pd.DataFrame, dict]:
    """Process OpenAlex data"""
    try:
        from Main.openAlex2xlsx import save_to_excel
        success = save_to_excel(input_file, output_file)
        
        if success:
            with ProcessIndicator("Converting OpenAlex data to Excel format") as indicator:
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

def merge_files(data_dir: str, db_name: str, file_ext: str) -> str:
    """Merge all files of a specific type in data directory into a single file in merged_raw folder"""
    files = find_files(os.path.join(data_dir, db_name), file_ext)
    if not files:
        raise ValueError(f"No {db_name} files ({file_ext}) found in Data/{db_name} folder.")
    
    # Create merged_raw directory if not exists
    merged_raw_dir = os.path.join(data_dir, "merged_raw")
    os.makedirs(merged_raw_dir, exist_ok=True)
    
    output_file = os.path.join(merged_raw_dir, f"{db_name}_raw.{file_ext}")
    
    if file_ext == 'txt':
        with open(output_file, 'w', encoding='utf-8') as outfile:
            for txt_file in files:
                with open(txt_file, 'r', encoding='utf-8') as infile:
                    outfile.write(infile.read())
                    outfile.write('\n')  # Add newline between files
    else:  # CSV files
        if len(files) == 1:
            import shutil
            shutil.copy2(files[0], output_file)
        else:
            all_data = []
            for csv_file in files:
                df = pd.read_csv(csv_file, encoding='utf-8')
                all_data.append(df)
            combined_df = pd.concat(all_data, ignore_index=True)
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
        
        # Database configuration
        db_config = {
            'wos': {'ext': 'txt', 'processor': process_wos_data},
            'scopus': {'ext': 'csv', 'processor': process_scopus_data},
            'pubmed': {'ext': 'csv', 'processor': process_pubmed_data},
            'dimensions': {'ext': 'csv', 'processor': process_dimensions_data},
            'cochrane': {'ext': 'csv', 'processor': process_cochrane_data},
            'lens': {'ext': 'csv', 'processor': process_lens_data},
            'openAlex': {'ext': 'csv', 'processor': process_openAlex_data}
        }
        
        # Create database directories if they don't exist
        for db_name in db_config.keys():
            db_dir = os.path.join(data_dir, db_name)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir)
        
        # Create merged_raw directory if not exists
        merged_raw_dir = os.path.join(data_dir, "merged_raw")
        if not os.path.exists(merged_raw_dir):
            os.makedirs(merged_raw_dir)
        
        # Process each database
        dataframes = []
        for db_name, config in db_config.items():
            db_dir = os.path.join(data_dir, db_name)
            if os.path.exists(db_dir) and os.listdir(db_dir):  # If directory exists and not empty
                try:
                    # Merge files if multiple files exist
                    raw_file = merge_files(data_dir, db_name, config['ext'])
                    
                    # Process the merged file
                    output_file = os.path.join(cell_files_dir, f"{db_name.capitalize()}.xlsx")
                    success, df, stats = config['processor'](raw_file, output_file)
                    
                    if success:
                        print(f"\n{db_name.capitalize()} Statistics:")
                        print(f"Records: {stats['Record Count']}")
                        print(f"Columns: {stats['Column Count']}")
                        dataframes.append(df)
                    
                except Exception as e:
                    sys.stderr.write(f"\nError processing {db_name}: {str(e)}\n")
        
        if not dataframes:
            sys.stderr.write("\nNo data could be processed!\n")
            return
        
        # Merge all databases
        print("\nMerging databases...")
        merged_df = merge_databases_enhanced(*dataframes)
        
        # Save merged results
        merged_output = os.path.join(cell_files_dir, f"Merged_Data_{timestamp}.xlsx")
        merged_df.to_excel(merged_output, index=False)
        
        print(f"\nMerged data saved to: {merged_output}")
        print(f"Total records: {len(merged_df)}")
        
    except Exception as e:
        sys.stderr.write(f"\nAn unexpected error occurred: {str(e)}\n")
    finally:
        print("\nProgram terminating...")

if __name__ == "__main__":
    main() 