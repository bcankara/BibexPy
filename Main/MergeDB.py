import pandas as pd
import re
import os
from typing import List, Union
import numpy as np

def trim(text: str) -> str:
    """Removes extra spaces from text"""
    if pd.isna(text):
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

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
    
    # Merge data frames
    M = pd.concat(dataframes, ignore_index=True)
    initial_size = len(M)
    
    # Organize database labels
    db_labels = pd.DataFrame({
        'DB': ['ISI', 'SCOPUS', 'OPENALEX', 'LENS', 'DIMENSIONS', 'PUBMED', 'COCHRANE'],
        'num': range(1, 8)
    })
    
    # Sort by databases
    M = M.merge(db_labels, on='DB', how='left')
    M = M.sort_values('num').drop('num', axis=1)
    
    # Rename CR column and add new CR column
    if 'CR' in M.columns:
        M = M.rename(columns={'CR': 'CR_raw'})
        M['CR'] = 'NA'
    
    def merge_values(series):
        if series.dtype == 'object':
            # Combine all non-null values
            combined = '; '.join(str(val) for val in series.dropna() if str(val).strip())
            return clean_merged_values(combined)
        return series.max()
    
    if remove_duplicated:
        if merge_fields:
            # Group by DOI and select the most complete data within each group
            if 'DI' in M.columns:
                # Group records with DOI
                grouped = M[~M['DI'].isna()].groupby('DI', as_index=False).agg(merge_values)
                
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
                grouped = M.groupby('title_year', as_index=False).agg(merge_values)
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
        M['DB_Original'] = M['DB']
        M['DB'] = 'ISI'
        
        # Clean author data
        if 'AU' in M.columns:
            def clean_author(au_str):
                if pd.isna(au_str):
                    return ""
                
                authors = []
                seen = set()
                for author in au_str.split(';'):
                    author = trim(author.replace(',', ' '))
                    parts = author.split()
                    if parts:
                        lastname = parts[0]
                        initials = ' '.join(p[0] for p in parts[1:] if p)
                        author_key = f"{lastname} {initials}"
                        if author_key not in seen:
                            seen.add(author_key)
                            authors.append(author_key)
                
                return '; '.join(authors)
            
            M['AU'] = M['AU'].apply(clean_author)
    
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