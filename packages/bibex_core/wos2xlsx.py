import pandas as pd
import re
import os


def remove_strange_char(text):
    # Remove strange characters
    return re.sub(r'[^\x00-\x7F]+', '', text)


def safe_str_replace(x, old, new):
    # Safe string replacement operation
    if pd.isna(x):
        return x
    try:
        return str(x).replace(old, new)
    except:
        return x


def isi2df(D):
    # Remove empty lines and strange characters
    D = [line for line in D if len(line.strip()) > 1]
    D = [remove_strange_char(line) for line in D]

    # Remove lines starting with FN and VR
    D = [line for line in D if not line.startswith(('FN ', 'VR '))]

    # Combine lines starting with spaces with the label of the previous line
    for i in range(1, len(D)):
        if D[i].startswith('   '):
            D[i] = D[i - 1][:3] + D[i][3:]

    # Find starting indices of documents
    papers = [i for i, line in enumerate(D) if line.startswith('PT ')]
    nP = len(papers)

    if nP == 0:
        raise ValueError("No valid ISI format found. Please check your file.")

    # Calculate number of lines for each document
    row_papers = []
    for i in range(len(papers) - 1):
        row_papers.append(papers[i + 1] - papers[i])
    row_papers.append(len(D) - papers[-1])

    # Create document number for each line
    num_papers = []
    for i, count in enumerate(row_papers):
        num_papers.extend([i + 1] * count)

    # Create DataFrame
    data = {
        'Tag': [line[:3].strip() for line in D],
        'content': [line[3:].strip() for line in D],
        'Paper': num_papers
    }
    df = pd.DataFrame(data)

    # Combine Tag and content
    df = df.groupby(['Paper', 'Tag'])['content'].apply('---'.join).reset_index()

    # Pivot operation
    df = df.pivot(index='Paper', columns='Tag', values='content').reset_index()

    # Check mandatory fields
    mandatory_tags = ['AU', 'DE', 'C1', 'RP', 'CR', 'PY', 'SO', 'TI', 'TC']
    missing_tags = [tag for tag in mandatory_tags if tag not in df.columns]
    if missing_tags:
        print("\nWarning: Some mandatory metadata fields are missing. Bibliometrix functions may not work properly!")
        print("Missing fields:", missing_tags)

    # Fields that should be separated by commas
    comma_tags = ['AU', 'AF', 'CR']
    for tag in comma_tags:
        if tag in df.columns:
            df[tag] = df[tag].apply(lambda x: safe_str_replace(x, '---', ';'))

    # Replace --- with space for other fields
    other_tags = [col for col in df.columns if col not in comma_tags]
    for tag in other_tags:
        if tag in df.columns:
            df[tag] = df[tag].apply(lambda x: safe_str_replace(x, '---', ' ').strip() if pd.notnull(x) else x)

    # Add C1raw column
    if 'C1' in df.columns:
        df['C1raw'] = df['C1'].copy()
        # Remove author information in square brackets
        df['C1'] = df['C1'].apply(lambda x: re.sub(r'\[.*?\]', '', str(x)) if pd.notnull(x) else x)
        df['C1'] = df['C1'].apply(lambda x: safe_str_replace(x, '.', '.;') if pd.notnull(x) else x)

    # Add database information
    df['DB'] = 'ISI'

    # Clean commas in author names
    if 'AU' in df.columns:
        df['AU'] = df['AU'].apply(lambda x: safe_str_replace(x, ',', ' ').strip() if pd.notnull(x) else x)

    # Convert all string columns to uppercase except DI
    di_col = None
    if 'DI' in df.columns:
        di_col = df['DI'].copy()

    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: str(x).upper() if pd.notnull(x) else x)

    if di_col is not None:
        df['DI'] = di_col

    # Remove Paper column
    df = df.drop('Paper', axis=1)

    return df


def save_to_excel(file_path, output_path):
    try:
        # Read file
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        if not lines:
            raise ValueError("File is empty!")

        # Convert data
        df = isi2df(lines)

        # Save as Excel
        df.to_excel(output_path, index=False)
        print(f"Data successfully saved to {output_path}")
        return True
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return False


if __name__ == "__main__":
    # Get file names from user
    print("Enter the name of the ISI format file (e.g., data.txt):")
    input_file = input().strip()

    print("Enter the name of the output Excel file (e.g., output.xlsx):")
    output_file = input().strip()

    # Check file extensions
    if not input_file.endswith(('.txt', '.isi')):
        input_file += '.txt'
    if not output_file.endswith('.xlsx'):
        output_file += '.xlsx'

    # Check if file exists
    if not os.path.exists(input_file):
        print(f"Error: File {input_file} not found!")
    else:
        # Process file and save to Excel
        success = save_to_excel(input_file, output_file)
        if success:
            print("\nOperation completed successfully!")