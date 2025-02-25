<p align="center">
  <img src="https://bibexpy.com/bibexpy_logo.webp" alt="BibexPy" width="250"/>
</p>

<h3 align="center">Harmonizing the Bibliometric Symphony of Scopus and Web of Science</h3>

<p align="center">
  <a href="https://www.python.org">
    <img src="https://img.shields.io/badge/Python-≥3.9-blue.svg?logo=python&logoColor=white" alt="Python"/>
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-GPL-green.svg" alt="License"/>
  </a>
  <a href="http://bibexpy.com/doc">
    <img src="https://img.shields.io/badge/docs-latest-brightgreen.svg" alt="Documentation"/>
  </a>
  <a href="https://github.com/bcankara/BibexPy/issues">
    <img src="https://img.shields.io/github/issues/bcankara/BibexPy.svg" alt="GitHub Issues"/>
  </a>
  <a href="https://github.com/bcankara/BibexPy/releases">
    <img src="https://img.shields.io/github/downloads/bcankara/BibexPy/total.svg" alt="Downloads"/>
  </a>
</p>

<p align="center">
  <a href="http://bibexpy.com/doc">Documentation</a> •
  <a href="#installation">Installation</a> •
  <a href="#features">Features</a> •
  <a href="#usage">Usage</a> •
  <a href="#support-and-community">Support</a>
</p>

## Google Colab Run
 [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/bcankara/BibexPy/blob/main/BibexPy.ipynb)
## Academic Citation

We appreciate the academic community's interest in BibexPy. If you find our tool useful in your research work, we kindly request that you cite our paper:

[![DOI](https://img.shields.io/badge/DOI-10.1016/j.softx.2025.102098-blue.svg)](https://doi.org/10.1016/j.softx.2025.102098)
[![ScienceDirect](https://img.shields.io/badge/ScienceDirect-View_Paper-orange.svg?logo=elsevier)](https://www.sciencedirect.com/science/article/pii/S2352711025000652)

### APA Citation Format

```
Kara, B. C., Şahin, A., & Dirsehan, T. (2025). BibexPy: Harmonizing the bibliometric symphony of Scopus and Web of Science. SoftwareX, 30, 102098. https://doi.org/10.1016/j.softx.2025.102098
```

### BibTeX Citation Format

```bibtex
@article{bibexpy2025,
    title     = {BibexPy: Harmonizing the bibliometric symphony of {Scopus} and {Web of Science}},
    author    = {Kara, Burak Can and {\c{S}}ahin, Alperen and Dirsehan, Ta{\c{s}}k{\i}n},
    journal   = {SoftwareX},
    volume    = {30},
    pages     = {102098},
    year      = {2025},
    issn      = {2352-7110},
    publisher = {Elsevier},
    doi       = {10.1016/j.softx.2025.102098},
    url       = {https://www.sciencedirect.com/science/article/pii/S2352711025000652},
    keywords  = {Bibliometric analysis tools, Automated data integration, Metadata enrichment software, Scikit-learn, Machine learning, API-Based metadata processing}
}
```

### IEEE Citation Format

```
B. C. Kara, A. Şahin and T. Dirsehan, "BibexPy: Harmonizing the bibliometric symphony of Scopus and Web of Science," SoftwareX, vol. 30, p. 102098, 2025, doi: 10.1016/j.softx.2025.102098.
```

### Chicago Citation Format

```
Kara, Burak Can, Alperen Şahin, and Taşkın Dirsehan. "BibexPy: Harmonizing the Bibliometric Symphony of Scopus and Web of Science." SoftwareX 30 (2025): 102098. https://doi.org/10.1016/j.softx.2025.102098.
```

---

BibexPy is a Python-based software designed to streamline bibliometric data integration, deduplication, metadata enrichment, and format conversion. It simplifies the preparation of high-quality datasets for advanced analyses by automating traditionally manual and error-prone tasks.

## Tech Stack

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-150458?style=for-the-badge&logo=pandas&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-013243?style=for-the-badge&logo=numpy&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikit-learn&logoColor=white)
![NLTK](https://img.shields.io/badge/NLTK-154F5B?style=for-the-badge&logo=python&logoColor=white)
![Excel](https://img.shields.io/badge/Excel-217346?style=for-the-badge&logo=microsoft-excel&logoColor=white)

![Scopus](https://img.shields.io/badge/Scopus-E9711C?style=for-the-badge&logo=elsevier&logoColor=white)
![Web of Science](https://img.shields.io/badge/Web_of_Science-000000?style=for-the-badge&logo=clarivate&logoColor=white)
![VOSviewer](https://img.shields.io/badge/VOSviewer-00A6D6?style=for-the-badge&logoColor=white)

## Features

- **DOI-Based Deduplication and Merging**: Identifies and removes duplicate entries while enriching metadata by merging complementary records.
- **Enhanced Metadata Enrichment**:
  - **API-Based Enrichment**: Completes missing fields using multiple APIs with detailed field statistics and API support information.
  - **Machine Learning Enrichment (Experimental)**:
    - Currently supports prediction for:
      - Keywords (DE field)
      - Keywords Plus (ID field) - Independent model using TF-IDF + RandomForest
      - Subject Categories (SC field)
      - Web of Science Categories (WC field)
    - Shows training data statistics for each field
    - Displays progress during model training
    - Provides enrichment results summary
    - Saves detailed statistics to Excel file
  - **Combined API + ML Enrichment**:
    - Sequential processing combining both methods
    - API enrichment performed first with user confirmation
    - ML enrichment applied to API-enriched data
    - Comprehensive statistics for both processes
    - User confirmation at each step
    - Automatic cleanup of temporary files
    - Detailed statistics saved to Excel files
- **Flexible Workflow**: Choose between API or ML enrichment in any order, with clear progress indicators and statistics.
- **Format Conversion**: Generates outputs compatible with VosViewer, Biblioshiny, and other analysis tools.
- **Command-Line Interface (CLI)**: Offers user-friendly interaction with minimal setup requirements.
- **Comprehensive Data Processing**: Handles multiple data sources and formats efficiently.

## Key Benefits

- **Time Saving**: Automates manual data cleaning and enrichment tasks
- **Enhanced Data Quality**: Reduces errors and inconsistencies in bibliometric data
- **Flexible Integration**: Works with multiple data sources and output formats
- **Rich Metadata**: Comprehensive metadata enrichment from multiple sources
- **Smart Enrichment**: Choose between API-based or ML-based enrichment methods
- **Detailed Feedback**: Clear statistics and progress indicators during processing
- **Easy to Use**: Simple command-line interface with clear instructions

## Prerequisites

### Required Python Version
- Python ≥ 3.9.0
  
### Required Libraries
```
# Core Libraries - Required for Basic Functionality
pandas>=2.0.0          # Data manipulation and analysis
numpy>=1.24.0          # Required by pandas for numerical operations
openpyxl>=3.1.2        # Excel file handling

# Machine Learning - Required for ML Enrichment
scikit-learn>=1.3.0    # ML-based metadata enrichment and predictions
nltk>=3.8.1            # Text processing and feature extraction

# API and Network Libraries - Required for API Enrichment
requests>=2.31.0       # API interactions for metadata enrichment
urllib3>=2.0.0         # HTTP client for Python, used by requests
certifi>=2023.5.7      # Required for SSL certificate verification
python-dotenv>=1.0.0   # API configuration management

# Progress and User Interface
tqdm>=4.65.0          # Progress tracking for long operations
colorama>=0.4.6        # Console output formatting and colors

# Utilities
unidecode==1.3.6       # Text normalization and cleaning
typing-extensions>=4.7.0  # Type hints support
```

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

1. **Basic Usage**
   ```bash
   python DataProcessor.py
   ```
   - Select your project
   - Upload Scopus (`.csv`) and Web of Science (`.txt`) files
   - Choose processing options

2. **Metadata Enrichment Options**

   The application offers three main methods for enriching your bibliometric data:

   **A. API-Based Enrichment**
   - Provides detailed statistics about empty fields
   - Shows which APIs support each field
   - Displays percentage of empty records for each field
   - Supports multiple APIs:
     - CrossRef (Free)
     - OpenAlex (Free)
     - DataCite (Free)
     - Europe PMC (Free)
     - Scopus (API key required)
     - Semantic Scholar (Optional API key)
     - Unpaywall (Email required)

   **B. Machine Learning Enrichment (Experimental)**
   - Currently supports prediction for:
     - Keywords (DE field)
     - Keywords Plus (ID field) - Independent model using TF-IDF + RandomForest
     - Subject Categories (SC field)
     - Web of Science Categories (WC field)
   - Shows training data statistics for each field
   - Displays progress during model training
   - Provides enrichment results summary
   - Saves detailed statistics to Excel file

   **C. Combined API + ML Enrichment**
   - Sequential processing combining both methods
   - API enrichment performed first with user confirmation
   - ML enrichment applied to API-enriched data
   - Comprehensive statistics for both processes
   - User confirmation at each step
   - Automatic cleanup of temporary files
   - Detailed statistics saved to Excel files

3. **API Configuration**

   For API-based enrichment, configure your APIs in `API_config.json`:

   ```json
   {
       "scopus": {
           "api_key": "YOUR-SCOPUS-API-KEY",
           "description": "Get your API key from https://dev.elsevier.com/"
       },
       "semantic_scholar": {
           "api_key": "YOUR-SEMANTIC-SCHOLAR-KEY",
           "description": "Optional. Get your key from https://www.semanticscholar.org/product/api"
       },
       "unpaywall": {
           "email": "your.institutional@email.edu",
           "description": "Use your institutional email for Unpaywall access"
       },
       "crossref": {
           "email": "your.contact@email.com",
           "description": "Recommended for better rate limits with CrossRef"
       }
   }
   ```

## Output Files and Formats

BibexPy generates several output files to support different analysis needs:

### 1. Unified Dataset (`Prefix_Bib.xlsx`)
- **Format**: Excel Workbook
- **Contents**:
  - Merged and deduplicated records
  - Enhanced metadata from multiple sources
  - Standardized author names and affiliations
  - Complete citation information
- **Uses**: 
  - Primary dataset for analysis
  - Input for other bibliometric tools
  - Reference database

### 2. VosViewer Export (`Prefix_Vos.txt`)
- **Format**: Tab-separated text file
- **Contents**:
  - Author and co-authorship data
  - Citation networks
  - Keyword co-occurrence
  - Institution collaborations
- **Uses**:
  - Direct import into VOSviewer
  - Network visualization
  - Cluster analysis

### 3. Quality Report (`Prefix_Quality.xlsx`)
- **Format**: Excel Workbook
- **Contents**:
  - Data completeness metrics
  - Field coverage statistics
  - Source distribution analysis
  - Duplicate detection results
  - API enrichment statistics
  - ML enrichment statistics
  - Field-wise enrichment rates
- **Uses**:
  - Dataset quality assessment
  - Coverage analysis
  - Source verification
  - Enrichment performance tracking

### 4. Analysis Summary (`Prefix_Summary.txt`)
- **Format**: Text file
- **Contents**:
  - Processing statistics
  - API enrichment results
  - Error logs and warnings
  - Data transformation details
- **Uses**:
  - Process verification
  - Quality control
  - Troubleshooting

## Support and Community

- **Issues and Bugs**: Submit via [GitHub Issues](https://github.com/bcankara/BibexPy/issues)
- **Feature Requests**: Use [GitHub Discussions](https://github.com/bcankara/BibexPy/discussions)
- **Questions**: Contact us at 📧 info@bibexpy.com
- **Updates**: Follow us on Twitter [@BibexPy](https://twitter.com/bibexpy)

## License

BibexPy is licensed under the [GNU General Public License (GPL)](LICENSE). See the LICENSE file for details.

---

Enhance your bibliometric research with BibexPy, making data preparation efficient, reliable, and analysis-ready!
