# BibexPy

[![GitHub](https://img.shields.io/github/license/bcankara/BibexPy)](https://github.com/bcankara/BibexPy/blob/Dev/LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)

BibexPy is a comprehensive bibliometric data processing tool designed to streamline the analysis of academic publications across multiple databases. It provides robust data merging, standardization, and analysis capabilities for bibliometric research.

## 🚀 Key Features

- **Multi-Database Support**
  - Web of Science (WoS)
  - Scopus
  - PubMed
  - Dimensions
  - Cochrane
  - Lens
  - OpenAlex

- **Advanced Data Processing**
  - Automated field mapping and standardization
  - Intelligent author name disambiguation
  - Institution information normalization
  - Journal abbreviation standardization
  - Citation format harmonization
  - Smart duplicate detection and removal

- **Enhanced Merging Capabilities**
  - Cross-database deduplication
  - Field-level intelligent merging
  - Metadata enrichment
  - Data quality validation

## 📁 Project Structure

```
BibexPy/
├── Main/                     # Core processing modules
│   ├── wos2xlsx.py          # WoS data converter
│   ├── scp2xlsx.py          # Scopus data converter
│   ├── pubmed2xlsx.py       # PubMed data converter
│   ├── dimensions2xlsx.py   # Dimensions data converter
│   ├── cochrane2xlsx.py     # Cochrane data converter
│   ├── lens2xlsx.py         # Lens data converter
│   ├── openAlex2xlsx.py     # OpenAlex data converter
│   └── MergeDB.py           # Database merging module
├── docs/                     # Documentation
├── Web/                      # Web interface components
├── Workspace/                # User project workspace
├── DataProcessor.py          # Main processing script
└── requirements.txt         # Project dependencies
```

## 🛠️ Installation

1. Clone the Dev branch:
```bash
git clone -b Dev https://github.com/bcankara/BibexPy.git
cd BibexPy
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment (optional):
   - Create a `.env` file in the root directory
   - Add your API keys if needed:
     ```
     SCOPUS_API_KEY=your_scopus_api_key
     UNPAYWALL_EMAIL=your_email
     ```

## 📂 Data Organization

After installation, follow these steps to organize your data:

1. Create your project folder in `Workspace/`:
```
Workspace/
└── YourProjectName/
```

2. The system will automatically create the following structure in your project folder:
```
YourProjectName/
├── Data/                    # Main data directory
│   ├── wos/                # Web of Science files (.txt)
│   ├── scopus/             # Scopus files (.csv)
│   ├── pubmed/             # PubMed files (.csv)
│   ├── dimensions/         # Dimensions files (.csv)
│   ├── cochrane/           # Cochrane files (.csv)
│   ├── lens/               # Lens files (.csv)
│   ├── openAlex/           # OpenAlex files (.csv)
│   └── merged_raw/         # Auto-generated merged files
└── Analysis_TIMESTAMP/      # Analysis results
    ├── Cell_Files/         # Excel outputs
    └── Text_Files/         # Text format outputs
```

3. Place your database files in the corresponding folders:
   - Web of Science: Place `.txt` files in `Data/wos/`
   - Scopus: Place `.csv` files in `Data/scopus/`
   - PubMed: Place `.csv` files in `Data/pubmed/`
   - Dimensions: Place `.csv` files in `Data/dimensions/`
   - Cochrane: Place `.csv` files in `Data/cochrane/`
   - Lens: Place `.csv` files in `Data/lens/`
   - OpenAlex: Place `.csv` files in `Data/openAlex/`

4. File Naming:
   - Files can have any name
   - Multiple files from the same database will be automatically merged
   - Original files are preserved

## 📊 Data Processing Workflow

1. **Data Organization**
   - Place database files in appropriate directories under your workspace
   - Supported formats:
     - WoS: `.txt` files
     - Others: `.csv` files

2. **Processing Pipeline**
   - Automated file detection and merging
   - Field standardization and mapping
   - Author and institution normalization
   - Citation format standardization
   - Cross-database deduplication

3. **Output Generation**
   - Excel format for detailed analysis
   - Standardized data structure
   - Comprehensive processing statistics
   - Quality validation reports

## 💻 Usage

1. Prepare your data files in the appropriate format

2. Run the processor:
```bash
python DataProcessor.py
```

3. Follow the interactive prompts to:
   - Select your project
   - Process your data
   - View statistics
   - Access merged results

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Thanks to all contributors who have helped shape this project
- Special thanks to the bibliometric research community for their valuable feedback

## 📧 Contact

For questions and feedback, please open an issue on GitHub or contact the maintainers directly through the repository.

---
Made with ❤️ by the BibexPy Team
