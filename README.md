# BibexPy

**Harmonizing the Bibliometric Symphony of Scopus and Web of Science**

BibexPy is a Python-based software designed to streamline bibliometric data integration, deduplication, metadata enrichment, and format conversion. It simplifies the preparation of high-quality datasets for advanced analyses by automating traditionally manual and error-prone tasks.

## Features

- **DOI-Based Deduplication and Merging**: Identifies and removes duplicate entries while enriching metadata by merging complementary records.
- **API-Driven Metadata Enrichment**: Completes missing fields such as abstracts, keywords, and affiliations using APIs like Unpaywall and Semantic Scholar.
- **Format Conversion**: Generates outputs compatible with VosViewer and Biblioshiny for easy analysis.
- **Command-Line Interface (CLI)**: Offers user-friendly interaction with minimal setup requirements.

# Prerequisites

## Required Python Version
- Python ≥ 3.9.0
  
**Libraries**:
  - `python-dotenv==1.0.0`
  - `pandas>=2.0.0`
  - `openpyxl>=3.1.2`
  - `numpy>=1.24.0`
  - `requests>=2.31.0`
  - `scikit-learn>=1.3.0`
  - `scipy>=1.11.0`
  - `tqdm>=4.65.0`
  - `xlrd>=2.0.1`
  - `xlsxwriter>=3.1.0`
  - `colorama >= 0.4.6`
  - `typing-extensions >= 4.7.0`


## Installation

1. **Clone the Repository**  
   ```bash
   git clone https://github.com/bcankara/BibexPy.git
   ```
2. **Navigate to the Directory**  
   ```bash
   cd BibexPy
   ```
3. **Install Dependencies**  
   ```bash
   pip install -r requirements.txt
   ```

4. **(Optional) Virtual Environment Setup**  
   ```bash
   python -m venv venv
   source venv/bin/activate  # Mac/Linux
   venv\Scripts\activate     # Windows
   ```

## Usage

1. **Run the Application**  
   ```bash
   python DataProcessor.py
   ```

2. **Follow the Workflow**  
   - Select your project.
   - Upload Scopus (`.csv`) and Web of Science (`.txt`) files.
   - Choose to enrich metadata (optional).
   - Review and save the processed datasets.

## Outputs

BibexPy generates the following outputs:
- Unified datasets (`Prefix_Bib.xlsx`).
- VosViewer-compatible files (`Prefix_Vos.txt`).
- Statistical summaries for dataset quality and completeness.

## Documentation

For detailed documentation and examples, visit: [BibexPy Documentation](http://bibexpy.com/doc)

## Support

For questions or feedback, contact:  
📧 **info@bibexpy.com**

## License

BibexPy is licensed under the **GNU General Public License (GPL)**. See the LICENSE file for details.

---

Enhance your bibliometric research with BibexPy, making data preparation efficient, reliable, and analysis-ready!
