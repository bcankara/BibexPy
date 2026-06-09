import pandas as pd
import os

def copy_cr_raw_to_cr(file_path: str) -> bool:
    """
    Copies CR_raw column to CR column
    
    Args:
        file_path (str): Path to Excel file
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Read Excel file
        df = pd.read_excel(file_path)
        
        # If CR_raw column exists and CR column is empty or doesn't exist
        if 'CR_raw' in df.columns:
            df['CR'] = df['CR_raw']
            
            # Save changes
            df.to_excel(file_path, index=False)
            print(f"\nCR_raw column copied to CR: {os.path.basename(file_path)}")
            return True
            
    except Exception as e:
        print(f"\nError: Failed to process file {file_path}: {str(e)}")
    return False

def process_merged_files(result_dir: str) -> None:
    """
    Copies CR_raw column to CR column in Merged_Bib.xlsx and 
    Merged_API_Enriched_Bib.xlsx files in Cell_Files folder
    
    Args:
        result_dir (str): Path to result directory
    """
    cell_files_dir = os.path.join(result_dir, "Cell_Files")
    
    if os.path.exists(cell_files_dir):
        # Files to process
        files_to_process = [
            os.path.join(cell_files_dir, "Merged_Bib.xlsx"),
            os.path.join(cell_files_dir, "Merged_API_Enriched_Bib.xlsx")
        ]
        
        for file_path in files_to_process:
            if os.path.exists(file_path):
                copy_cr_raw_to_cr(file_path) 