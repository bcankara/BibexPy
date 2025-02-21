# BibexPy <img src="https://bibexpy.com/bibexpy_logo.webp" alt="BibexPy Logo" width="80" align="right"/>

<div align="center">
  <img src="https://bibexpy.com/bibexpy_logo.webp" alt="BibexPy" width="250"/>
  <h3>Harmonizing the Bibliometric Symphony of Scopus and Web of Science</h3>
</div>

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

## Academic Citation

We appreciate the academic community's interest in BibexPy. If you find our tool useful in your research work, we kindly request that you cite our paper:

<div align="center" style="display: flex; justify-content: center; gap: 10px;">
  <a href="https://doi.org/10.1016/j.softx.2025.102098">
    <img src="https://img.shields.io/badge/DOI-10.1016/j.softx.2025.102098-blue.svg" width="200" alt="DOI"/>
  </a>
  <a href="https://www.sciencedirect.com/science/article/pii/S2352711025000652">
    <img src="https://img.shields.io/badge/ScienceDirect-View_Paper-orange.svg?logo=elsevier" width="200" alt="ScienceDirect"/>
  </a>
</div>

<div align="center">
  <div style="max-width: 800px; margin: 20px auto; padding: 20px; background-color: #f8f9fa; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
    <h3>📝 Recommended Citation Format (APA 7th Edition)</h3>
    <div style="position: relative; padding: 15px; background-color: white; border-radius: 4px; margin: 10px 0;">
      <button class="copy-button" onclick="copyToClipboard('apa-citation')" style="position: absolute; top: 10px; right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
        Copy
      </button>
      <code id="apa-citation">Kara, B. C., Şahin, A., & Dirsehan, T. (2025). BibexPy: Harmonizing the bibliometric symphony of Scopus and Web of Science. SoftwareX, 30, 102098. https://doi.org/10.1016/j.softx.2025.102098</code>
    </div>
  </div>
</div>

<details>
<summary><b>🔍 Other Citation Formats</b></summary>

<div style="margin: 20px 0;">
  <h4>BibTeX</h4>
  <div style="position: relative; padding: 15px; background-color: #f8f9fa; border-radius: 4px;">
    <button class="copy-button" onclick="copyToClipboard('bibtex-citation')" style="position: absolute; top: 10px; right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
      Copy
    </button>
    <pre><code id="bibtex-citation">@article{bibexpy2025,
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
}</code></pre>
  </div>

  <h4>IEEE</h4>
  <div style="position: relative; padding: 15px; background-color: #f8f9fa; border-radius: 4px;">
    <button class="copy-button" onclick="copyToClipboard('ieee-citation')" style="position: absolute; top: 10px; right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
      Copy
    </button>
    <code id="ieee-citation">B. C. Kara, A. Şahin and T. Dirsehan, "BibexPy: Harmonizing the bibliometric symphony of Scopus and Web of Science," SoftwareX, vol. 30, p. 102098, 2025, doi: 10.1016/j.softx.2025.102098.</code>
  </div>

  <h4>Chicago</h4>
  <div style="position: relative; padding: 15px; background-color: #f8f9fa; border-radius: 4px;">
    <button class="copy-button" onclick="copyToClipboard('chicago-citation')" style="position: absolute; top: 10px; right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
      Copy
    </button>
    <code id="chicago-citation">Kara, Burak Can, Alperen Şahin, and Taşkın Dirsehan. "BibexPy: Harmonizing the Bibliometric Symphony of Scopus and Web of Science." SoftwareX 30 (2025): 102098. https://doi.org/10.1016/j.softx.2025.102098.</code>
  </div>
</div>
</details>

<script>
function copyToClipboard(elementId) {
  const element = document.getElementById(elementId);
  const text = element.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const button = element.parentElement.querySelector('.copy-button');
    button.textContent = 'Copied!';
    setTimeout(() => {
      button.textContent = 'Copy';
    }, 2000);
  });
}
</script>

---

BibexPy is a Python-based software designed to streamline bibliometric data integration, deduplication, metadata enrichment, and format conversion. It simplifies the preparation of high-quality datasets for advanced analyses by automating traditionally manual and error-prone tasks.

## Tech Stack

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Pandas-150458?style=for-the-badge&logo=pandas&logoColor=white" alt="Pandas"/>
  <img src="https://img.shields.io/badge/NumPy-013243?style=for-the-badge&logo=numpy&logoColor=white" alt="NumPy"/>
  <img src="https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikit-learn&logoColor=white" alt="scikit-learn"/>
  <img src="https://img.shields.io/badge/NLTK-154F5B?style=for-the-badge&logo=python&logoColor=white" alt="NLTK"/>
  <img src="https://img.shields.io/badge/Excel-217346?style=for-the-badge&logo=microsoft-excel&logoColor=white" alt="Excel"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Scopus-E9711C?style=for-the-badge&logo=elsevier&logoColor=white" alt="Scopus"/>
  <img src="https://img.shields.io/badge/Web_of_Science-000000?style=for-the-badge&logo=clarivate&logoColor=white" alt="Web of Science"/>
  <img src="https://img.shields.io/badge/VOSviewer-00A6D6?style=for-the-badge&logoColor=white" alt="VOSviewer"/>
</p>

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

# Prerequisites

## Required Python Version
- Python ≥ 3.9.0
  
## Required Libraries
- `pandas>=2.0.0`          # Data manipulation and analysis
- `numpy>=1.24.0`          # Required by pandas for numerical operations
- `openpyxl>=3.1.2`        # Excel file handling
- `scikit-learn>=1.2.0`    # ML-based metadata enrichment
- `nltk>=3.8.1`            # Text processing for ML features
- `requests>=2.31.0`       # API interactions for metadata enrichment
- `tqdm>=4.65.0`          # Progress tracking
- `colorama>=0.4.6`        # Console output formatting
- `unidecode==1.3.6`       # Text normalization
- `typing-extensions>=4.7.0`  # Type hints support

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

   **Flexible Workflow**
   - Choose any enrichment method
   - Clear statistics and progress tracking
   - Independent or combined processing options
   - User control over the enrichment process

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

## Data Fields

The unified dataset includes standardized fields from both Scopus and Web of Science:

- **Document Identification**
  - DOI, Title, Authors, Year
  - Source details (Journal, Volume, Issue)
  - Document type and language

- **Citation Information**
  - Citation counts from multiple sources
  - Reference lists
  - Citation context (when available)

- **Author Information**
  - Standardized author names
  - Author affiliations
  - Corresponding author details

- **Content Classification**
  - Keywords (author and index)
  - Research areas
  - Subject categories

- **Additional Metadata**
  - Abstracts
  - Funding information
  - Open access status
  - URLs and identifiers

## Documentation

For detailed documentation, including:
- Advanced usage examples
- API integration guides
- Field mapping references
- Troubleshooting tips

Visit: [BibexPy Documentation](http://bibexpy.com/doc)

## Support and Community

- **Issues and Bugs**: Submit via GitHub Issues
- **Feature Requests**: Use GitHub Discussions
- **Questions**: Contact us at 📧 **info@bibexpy.com**
- **Updates**: Follow us on Twitter [@BibexPy](https://twitter.com/bibexpy)

## License

BibexPy is licensed under the **GNU General Public License (GPL)**. See the LICENSE file for details.

---

Enhance your bibliometric research with BibexPy, making data preparation efficient, reliable, and analysis-ready!
