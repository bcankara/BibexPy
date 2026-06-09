import pandas as pd
from tqdm import tqdm  # İlerleme çubuğu için tqdm kütüphanesi

def convert_excel_to_wos(input_excel_path, output_txt_path):
    # Excel dosyasını pandas kullanarak aç
    df = pd.read_excel(input_excel_path)

    # Başlıklara göre sütun isimlerini eşleştir
    desired_columns = {
        "PT": "PT",           # Publication Type
        "AU": "AU",           # Authors
        "AF": "AF",           # Author Full Names
        "TI": "TI",           # Title
        "SO": "SO",           # Source
        "LA": "LA",           # Language
        "DT": "DT",           # Document Type
        "DE": "DE",           # Keywords
        "ID": "ID",           # Keywords Plus
        "AB": "AB",           # Abstract
        "C1": "C1",        # Author Address
        "C3": "C3",           # Additional Address
        "RP": "RP",           # Reprint Address
        "EM": "EM",           # Email
        "FU": "FU",           # Funding
        "FX": "FX",           # Funding Text
        "CR": "CR",       # Cited References
        "NR": "NR",           # Number of References
        "TC": "TC",           # Times Cited
        "Z9": "Z9",           # Total Times Cited
        "U1": "U1",           # Usage Count (Last 180 Days)
        "U2": "U2",           # Usage Count (Since 2013)
        "PU": "PU",           # Publisher
        "PI": "PI",           # Publisher City
        "PA": "PA",           # Publisher Address
        "SN": "SN",           # ISSN
        "EI": "ISSN",         # Electronic ISSN
        "J9": "J9",           # 29-Character Source Abbreviation
        "JI": "JI",           # ISO Source Abbreviation
        "PD": "PD",           # Publication Date
        "PY": "PY",           # Year Published
        "VL": "VL",           # Volume
        "AR": "Art. No.",     # Article Number
        "DI": "DI",           # DOI
        "EA": "EA",           # Early Access Date
        "PG": "PG",           # Page Count
        "WC": "WC",           # Web of Science Categories
        "WE": "WE",           # Web of Science Index
        "SC": "SC",           # Research Areas
        "GA": "GA",           # Document Delivery Number
        "UT": "UT",           # Accession Number
        "DA": "DA"            # Date of Export
    }

    # Değerleri saklamak için boş listeler oluştur
    values_dict = {column: [] for column in desired_columns}

    # Verileri başlıklara göre al
    for idx, row in df.iterrows():
        for key, header_name in desired_columns.items():
            cell_value = row.get(header_name, "")
            values_dict[key].append(cell_value if pd.notna(cell_value) else "")

    with open(output_txt_path, 'w', encoding='utf-8') as file:
        # Dosya başlangıcı (bir kere)
        file.write("FN Clarivate Analytics Web of Science\n")
        file.write("VR 1.0\n\n")

        # Her kayıt için bilgileri dosyaya yaz
        for idx in range(len(df)):
            # Her kayıt PT ile başlar
            pt_value = values_dict["PT"][idx] if values_dict["PT"][idx] else "J"
            file.write(f"PT {pt_value}\n")

            # AU (Author) bilgisi
            au = values_dict["AU"][idx]
            if au:
                au_list = au.split(';')  # Yazarları ayır
                formatted_au_list = [author.strip() for author in au_list if author.strip()]
                file.write(f"AU {formatted_au_list[0]}\n")  # AU belirtecinin ilk satırını AU satırına yaz
                for author in formatted_au_list[1:]:
                    file.write(f"   {author}\n")  # Diğer yazarları alt satırlara yaz ve önde üç boşluk bırak
            else:
                file.write("AU \n")

            # AF (Author Full Name) bilgisi
            af = values_dict["AF"][idx]
            if af:
                af_list = af.split(';')  # Yazar tam isimlerini ayır
                file.write(f"AF {af_list[0].strip()}\n")  # AF belirtecinin ilk satırını AF satırına yaz
                for author_full in af_list[1:]:
                    file.write(f"   {author_full.strip()}\n")  # Diğer isimleri alt satırlara yaz ve önde üç boşluk bırak
            else:
                file.write("AF \n")

            # Diğer makale bilgilerini yaz
            file.write(f"TI {values_dict['TI'][idx]}\n")  # Makale başlığı
            file.write(f"SO {values_dict['SO'][idx]}\n")  # Kaynak başlık (Source)
            file.write(f"LA {values_dict['LA'][idx]}\n")  # Dil bilgisi (Language)
            file.write(f"DT {values_dict['DT'][idx]}\n")  # Belge türü (Document Type)
            file.write(f"DE {values_dict['DE'][idx]}\n")  # Anahtar kelimeler (Keywords)
            file.write(f"ID {values_dict['ID'][idx]}\n")  # Keywords Plus
            file.write(f"AB {values_dict['AB'][idx]}\n")  # Özet (Abstract)

            # C1 (Author Address) information - Each address on a new line
            c1 = values_dict['C1'][idx]
            af = values_dict['AF'][idx]

            if c1 and af:
                # Split authors and addresses
                authors = [a.strip() for a in af.split(';') if a.strip()]
                addresses = [addr.strip() for addr in c1.split(';') if addr.strip()]
                
                # If we have both authors and addresses
                if authors and addresses:
                    # İlk satır için "C1" kullan
                    file.write(f"C1 [{authors[0]}] {addresses[0]}\n")
                    
                    # Diğer satırlar için "   " ile başla
                    current_addr_idx = 1
                    
                    # Normal eşleşmeleri yaz
                    for i in range(1, min(len(authors), len(addresses))):
                        file.write(f"   [{authors[i]}] {addresses[i]}\n")
                        current_addr_idx = i + 1
                        
                    # Eğer fazla yazar varsa, son adresle eşleştir
                    if len(authors) > len(addresses):
                        last_address = addresses[-1]
                        for i in range(current_addr_idx, len(authors)):
                            file.write(f"   [{authors[i]}] {last_address}\n")
            else:
                file.write("C1 \n")

            file.write(f"C3 {values_dict['C3'][idx]}\n")  # Other address information
            file.write(f"RP {values_dict['RP'][idx]}\n")  # Reprint Address
            file.write(f"EM {values_dict['EM'][idx]}\n")  # Email Address
            file.write(f"FU {values_dict['FU'][idx]}\n")  # Funding Information
            file.write(f"FX {values_dict['FX'][idx]}\n")  # Funding Text

            # CR (Cited References) information - from CR_raw column
            cr = values_dict['CR'][idx]
            if cr:
                cr_list = [ref.strip() for ref in cr.split(';') if ref.strip()]  # Filter empty references
                if cr_list:
                    # Writing process
                    file.write(f"CR {cr_list[0]}\n")  # First reference
                    for ref in cr_list[1:]:
                        file.write(f"   {ref}\n")  # Other references start with 3 spaces
            else:
                file.write("CR \n")

            # Diğer gerekli bilgileri yaz
            file.write(f"NR {values_dict['NR'][idx]}\n")  # Atıf sayısı (Number of References)
            file.write(f"TC {values_dict['TC'][idx]}\n")  # Toplam atıf sayısı (Times Cited)
            file.write(f"Z9 {values_dict['Z9'][idx]}\n")  # Total Times Cited
            file.write(f"U1 {values_dict['U1'][idx]}\n")  # Usage Count (Last 180 Days)
            file.write(f"U2 {values_dict['U2'][idx]}\n")  # Usage Count (Since 2013)
            file.write(f"PU {values_dict['PU'][idx]}\n")  # Publisher
            file.write(f"PI {values_dict['PI'][idx]}\n")  # Publisher City
            file.write(f"PA {values_dict['PA'][idx]}\n")  # Publisher Address
            file.write(f"SN {values_dict['SN'][idx]}\n")  # ISSN
            file.write(f"EI {values_dict['EI'][idx]}\n")  # Electronic ISSN
            file.write(f"J9 {values_dict['J9'][idx]}\n")  # 29-Character Source Abbreviation
            file.write(f"JI {values_dict['JI'][idx]}\n")  # ISO Source Abbreviation
            file.write(f"PD {values_dict['PD'][idx]}\n")  # Publication Date
            file.write(f"PY {values_dict['PY'][idx]}\n")  # Year Published
            file.write(f"VL {values_dict['VL'][idx]}\n")  # Volume
            file.write(f"AR {values_dict['AR'][idx]}\n")  # Article Number
            file.write(f"DI {values_dict['DI'][idx]}\n")  # DOI
            file.write(f"EA {values_dict['EA'][idx]}\n")  # Early Access Date
            file.write(f"PG {values_dict['PG'][idx]}\n")  # Page Count
            file.write(f"WC {values_dict['WC'][idx]}\n")  # Web of Science Categories
            file.write(f"WE {values_dict['WE'][idx]}\n")  # Web of Science Index
            file.write(f"SC {values_dict['SC'][idx]}\n")  # Research Areas
            file.write(f"GA {values_dict['GA'][idx]}\n")  # Document Delivery Number
            file.write(f"UT {values_dict['UT'][idx]}\n")  # Accession Number
            file.write(f"DA {values_dict['DA'][idx]}\n")  # Date of Export

            # Her kayıt ER ile biter
            file.write("ER\n\n")

        # Dosya sonu
        file.write("EF\n")

    print(f"Successfully converted file to WoS format: '{output_txt_path}'")
