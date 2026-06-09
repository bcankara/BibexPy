import pandas as pd
import re
import os
from typing import List, Union


def abbrev_title(title: str) -> str:
    # This function can be used to abbreviate journal names
    # Simple version for now
    return title.replace('.', '').upper()


def labelling(data: pd.DataFrame) -> pd.DataFrame:
    """Converts column labels to standard format"""

    df_tag = pd.DataFrame([
        ["Abbreviated Source Title", "JI"],
        ["Affiliations", "C1"],
        ["Authors", "AU"],
        ["Author Names", "AU"],
        ["Author full names", "AF"],
        ["Source title", "SO"],
        ["Titles", "TI"],
        ["Title", "TI"],
        ["Publication Year", "PY"],
        ["Year", "PY"],
        ["Volume", "VL"],
        ["Issue", "IS"],
        ["Page count", "PP"],
        ["Cited by", "TC"],
        ["DOI", "DI"],
        ["Link", "URL"],
        ["Abstract", "AB"],
        ["Author Keywords", "DE"],
        ["Indexed Keywords", "ID"],
        ["Index Keywords", "ID"],
        ["Funding Details", "FU"],
        ["Funding Texts", "FX"],
        ["Funding Text 1", "FX"],
        ["References", "CR"],
        ["Correspondence Address", "RP"],
        ["Publisher", "PU"],
        ["Open Access", "OA"],
        ["Language of Original Document", "LA"],
        ["Document Type", "DT"],
        ["Source", "DB"],
        ["EID", "UT"]
    ], columns=['orig', 'tag'])

    # Mevcut sütun isimlerini al
    label = pd.DataFrame({'orig': data.columns})

    # Eşleştirme yap
    label = label.merge(df_tag, on='orig', how='left')

    # Eşleşmeyen sütunlar için orijinal ismi kullan
    label['tag'] = label['tag'].fillna(label['orig'])

    # Sütun isimlerini güncelle
    data.columns = label['tag']

    return data


def csvScopus2df(files: Union[str, List[str]]) -> pd.DataFrame:
    """
    Reads and processes Scopus CSV files

    Parameters:
    -----------
    files : str or list of str
        Path to CSV files to process

    Returns:
    --------
    pd.DataFrame
        Processed data frame
    """

    if isinstance(files, str):
        files = [files]

    all_data = []

    for i, file in enumerate(files):
        try:
            # Read CSV file
            df = pd.read_csv(file,
                             dtype=str,  # Read all columns as string
                             na_values='',  # Mark empty values as NA
                             keep_default_na=False,  # Don't use default NA values
                             encoding='utf-8')  # Use UTF-8 encoding

            # Replace NA values with empty string
            df = df.fillna('')

            if i > 0:
                # Find common columns
                common_cols = list(set(df.columns) & set(all_data[0].columns))
                # Merge using only common columns
                all_data.append(df[common_cols])
            else:
                all_data.append(df)

        except Exception as e:
            print(f"Error: Failed to read file {file}: {str(e)}")
            continue

    if not all_data:
        raise ValueError("No files could be read!")

    # Merge all dataframes
    DATA = pd.concat(all_data, ignore_index=True)

    # Organize column labels
    DATA = labelling(DATA)

    # Clean author names
    if 'AU' in DATA.columns:
        DATA['AU'] = DATA['AU'].str.replace('.', '', regex=False)
        DATA['AU'] = DATA['AU'].str.replace(',', ';', regex=False)

    # Store raw institution information
    if 'C1' not in DATA.columns:
        DATA['C1'] = ''

    # Process journal abbreviations
    if 'JI' in DATA.columns:
        DATA['J9'] = DATA['JI'].str.replace('.', '', regex=False)
    else:
        if 'SO' in DATA.columns:
            DATA['J9'] = DATA['SO'].apply(abbrev_title)
            DATA['JI'] = DATA['J9']

    # Store DI and URL values (before converting to uppercase)
    di_values = DATA.get('DI', None)
    url_values = DATA.get('URL', None)

    # Convert all string columns to uppercase
    for col in DATA.columns:
        if DATA[col].dtype == 'object':
            DATA[col] = DATA[col].str.upper()

    # Restore DI and URL values
    if di_values is not None:
        DATA['DI'] = di_values
    if url_values is not None:
        DATA['URL'] = url_values

    return DATA


def save_to_excel(file_paths: Union[str, List[str]], output_path: str) -> bool:
    """
    Processes Scopus CSV files and saves as Excel file

    Parameters:
    -----------
    file_paths : str or list of str
        Path to CSV files to process
    output_path : str
        Path to output Excel file

    Returns:
    --------
    bool
        Whether the operation was successful
    """
    try:
        # Convert data
        df = csvScopus2df(file_paths)

        # Save as Excel
        df.to_excel(output_path, index=False)
        print(f"Data successfully saved to {output_path}")
        return True
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return False


if __name__ == "__main__":
    try:
        # Show current working directory
        print(f"Current working directory: {os.getcwd()}")

        while True:
            # Get file names from user
            print("\nEnter the full path or file names of Scopus CSV files separated by commas")
            print("Example 1: data1.csv,data2.csv")
            print("Example 2: C:/Users/data1.csv,D:/Data/data2.csv")
            print("(Type 'q' to quit)")

            input_str = input().strip()
            if input_str.lower() == 'q':
                print("Program terminating...")
                break

            input_files = [f.strip() for f in input_str.split(',')]

            # Get output file name
            print("\nEnter the name or full path of the output Excel file")
            print("Example 1: output.xlsx")
            print("Example 2: C:/Users/output.xlsx")
            output_file = input().strip()

            # Check file extensions
            input_files = [f if f.endswith('.csv') else f + '.csv' for f in input_files]
            if not output_file.endswith('.xlsx'):
                output_file += '.xlsx'

            # Check if files exist
            missing_files = [f for f in input_files if not os.path.exists(f)]
            if missing_files:
                print("\nError: The following files were not found:")
                for f in missing_files:
                    print(f"- {f}")
                print("\nPlease check the file names and try again.")
                continue

            # Process files and save to Excel
            print("\nProcessing files...")
            success = save_to_excel(input_files, output_file)
            if success:
                print("\nOperation completed successfully!")
                print(f"Result file: {os.path.abspath(output_file)}")

            # Ask if user wants to continue
            print("\nDo you want to process more files? (Y/N)")
            if input().strip().upper() != 'Y':
                print("Program terminating...")
                break

    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}")