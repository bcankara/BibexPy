import pandas as pd
import re
import os
from typing import List, Union


def abbrev_title(title: str) -> str:
    return title.replace('.', '').upper()


def labelling(data: pd.DataFrame) -> pd.DataFrame:
    """Converts Cochrane column labels to standard format"""
    
    df_tag = pd.DataFrame([
        ["Journal", "SO"],
        ["Title", "TI"],
        ["Abstract", "AB"],
        ["Authors", "AU"],
        ["Author Address", "C1"],
        ["Year", "PY"],
        ["Volume", "VL"],
        ["Issue", "IS"],
        ["Pages", "PP"],
        ["DOI", "DI"],
        ["References", "CR"],
        ["Keywords", "DE"],
        ["Publication Type", "DT"],
        ["Language", "LA"],
        ["Publisher", "PU"],
        ["ISSN", "SN"],
        ["Accession Number", "UT"]
    ], columns=['orig', 'tag'])

    # Get current column names
    label = pd.DataFrame({'orig': data.columns})

    # Match with standard tags
    label = label.merge(df_tag, on='orig', how='left')

    # Use original name for unmatched columns
    label['tag'] = label['tag'].fillna(label['orig'])

    # Update column names
    data.columns = label['tag']

    return data


def csvCochrane2df(files: Union[str, List[str]]) -> pd.DataFrame:
    """
    Reads and processes Cochrane CSV files

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
                           dtype=str,
                           na_values='',
                           keep_default_na=False,
                           encoding='utf-8')

            # Replace NA values with empty string
            df = df.fillna('')

            if i > 0:
                common_cols = list(set(df.columns) & set(all_data[0].columns))
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
    if 'C1' in DATA.columns:
        DATA['C1raw'] = DATA['C1'].copy()

        def process_affiliation(aff):
            if pd.isna(aff) or aff == '':
                return ''
            parts = aff.split(';')
            processed = []
            for part in parts:
                processed.append(re.sub(r'.*?, ', '', part))
            return ';'.join(processed)

        DATA['C1'] = DATA['C1'].apply(process_affiliation)
    else:
        DATA['C1'] = ''

    # Process journal abbreviations
    if 'JI' in DATA.columns:
        DATA['J9'] = DATA['JI'].str.replace('.', '', regex=False)
    else:
        if 'SO' in DATA.columns:
            DATA['J9'] = DATA['SO'].apply(abbrev_title)
            DATA['JI'] = DATA['J9']

    # Add database identifier
    DATA['DB'] = 'COCHRANE'

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
    Processes Cochrane CSV files and saves as Excel file

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
        df = csvCochrane2df(file_paths)

        # Save as Excel
        df.to_excel(output_path, index=False)
        print(f"Data successfully saved to {output_path}")
        return True
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return False


if __name__ == "__main__":
    try:
        print(f"Current working directory: {os.getcwd()}")

        while True:
            print("\nEnter the full path or file names of Cochrane CSV files separated by commas")
            print("Example 1: data1.csv,data2.csv")
            print("Example 2: C:/Users/data1.csv,D:/Data/data2.csv")
            print("(Type 'q' to quit)")

            input_str = input().strip()
            if input_str.lower() == 'q':
                print("Program terminating...")
                break

            input_files = [f.strip() for f in input_str.split(',')]

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

            print("\nDo you want to process more files? (Y/N)")
            if input().strip().upper() != 'Y':
                print("Program terminating...")
                break

    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}") 