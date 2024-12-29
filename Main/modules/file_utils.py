import os
import glob
from typing import List
import pandas as pd
from datetime import datetime

def ensure_dir(directory: str):
    """Ensure directory exists, create if not"""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")

def find_files(directory: str, extension: str) -> List[str]:
    """Find files with given extension in specified directory"""
    pattern = os.path.join(directory, f"*.{extension}")
    return glob.glob(pattern)

def find_data_folders() -> List[str]:
    """Find folders with Data_ prefix"""
    return [d for d in os.listdir() if os.path.isdir(d) and d.startswith('Data_')]

def save_statistics(stats: dict, output_file: str):
    """Save statistics to file"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"Data Processing Statistics - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*50 + "\n\n")
        
        for key, value in stats.items():
            f.write(f"{key}:\n")
            if isinstance(value, dict):
                for k, v in value.items():
                    f.write(f"  - {k}: {v}\n")
            else:
                f.write(f"  {value}\n")
            f.write("\n")

def save_comprehensive_statistics(stats: dict, wos_df: pd.DataFrame, scopus_df: pd.DataFrame, 
                                merged_df: pd.DataFrame, output_file: str, simple_df: pd.DataFrame = None):
    """Save all statistics and analyses to Excel file"""
    from .stats_utils import generate_metadata_statistics, generate_metadata_comparison
    
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        # Save basic statistics
        stats_rows = []
        for category, data in stats.items():
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        for subkey, subvalue in value.items():
                            stats_rows.append([category, f"{key} - {subkey}", subvalue])
                    else:
                        stats_rows.append([category, key, value])
            else:
                stats_rows.append([category, "", data])
        
        stats_df = pd.DataFrame(stats_rows, columns=['Category', 'Metric', 'Value'])
        stats_df.to_excel(writer, sheet_name='General', index=False)

        # Save metadata statistics
        wos_metadata_stats = generate_metadata_statistics(wos_df)
        scopus_metadata_stats = generate_metadata_statistics(scopus_df)
        merged_metadata_stats = generate_metadata_statistics(merged_df)

        wos_metadata_stats.to_excel(writer, sheet_name='WoS Metadata', index=False)
        scopus_metadata_stats.to_excel(writer, sheet_name='Scopus Metadata', index=False)
        merged_metadata_stats.to_excel(writer, sheet_name='Merged Metadata', index=False)

        # Save metadata comparison statistics
        if simple_df is not None:
            metadata_comparison = generate_metadata_comparison(simple_df, merged_df)
            metadata_comparison.to_excel(writer, sheet_name='Metadata Comparison', index=False) 