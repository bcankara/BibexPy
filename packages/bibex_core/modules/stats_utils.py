import pandas as pd
from datetime import datetime

def generate_detailed_statistics(wos_df: pd.DataFrame, scopus_df: pd.DataFrame, merged_df: pd.DataFrame) -> dict:
    """Generate detailed statistics"""
    # Basic statistics
    basic_stats = {
        'General Statistics': {
            'Total WoS Records': len(wos_df),
            'Total Scopus Records': len(scopus_df),
            'Merged Records': len(merged_df),
            'Merge Rate': f"{(len(merged_df) / (len(wos_df) + len(scopus_df)) * 100):.2f}%"
        }
    }

    # Column statistics
    column_stats = {
        'Column Statistics': {
            'WoS Column Count': len(wos_df.columns),
            'Scopus Column Count': len(scopus_df.columns),
            'Merged Column Count': len(merged_df.columns),
            'Common Column Count': len(set(wos_df.columns) & set(scopus_df.columns))
        }
    }

    # Data quality statistics
    quality_stats = {
        'Data Quality': {
            'WoS Empty Cell Rate': f"{(wos_df.isna().sum().sum() / (len(wos_df) * len(wos_df.columns)) * 100):.2f}%",
            'Scopus Empty Cell Rate': f"{(scopus_df.isna().sum().sum() / (len(scopus_df) * len(scopus_df.columns)) * 100):.2f}%",
            'Merged Empty Cell Rate': f"{(merged_df.isna().sum().sum() / (len(merged_df) * len(merged_df.columns)) * 100):.2f}%"
        }
    }

    # Year-based statistics
    if 'Year' in merged_df.columns:
        year_stats = merged_df['Year'].value_counts().sort_index().to_dict()
        year_distribution = {'Year Distribution': year_stats}
    else:
        year_distribution = {'Year Distribution': {'Year information not found': 0}}

    return {**basic_stats, **column_stats, **quality_stats, **year_distribution}

def generate_metadata_statistics(df: pd.DataFrame) -> pd.DataFrame:
    """Generate quality statistics for metadata fields"""
    metadata_desc = {
        'AU': 'Author',
        'DT': 'Document Type',
        'PY': 'Publication Year',
        'TI': 'Title',
        'TC': 'Total Citation',
        'SO': 'Journal',
        'AB': 'Abstract',
        'C1': 'Affiliation',
        'DI': 'DOI',
        'RP': 'Corresponding Author',
        'ID': 'Keywords Plus',
        'WC': 'Science Categories',
        'DE': 'Keywords',
        'LA': 'Language',
        'CR': 'Cited References'
    }
    
    stats = []
    total_docs = len(df)
    
    for field, desc in metadata_desc.items():
        if field in df.columns:
            missing = df[field].isna().sum() + df[field].eq('').sum()
            missing_pct = (missing / total_docs) * 100
            
            # Determine status based on missing percentage
            if missing_pct == 0:
                status = 'Excellent'
            elif missing_pct < 1:
                status = 'Very Good'
            elif missing_pct < 5:
                status = 'Good'
            elif missing_pct < 20:
                status = 'Acceptable'
            elif missing_pct < 50:
                status = 'Poor'
            elif missing_pct < 90:
                status = 'Critical'
            else:
                status = 'Completely Missing'
            
            stats.append({
                'Field': field,
                'Description': desc,
                'Missing Count': missing,
                'Missing %': round(missing_pct, 2),
                'Status': status
            })
    
    return pd.DataFrame(stats)

def generate_metadata_comparison(simple_df: pd.DataFrame, enhanced_df: pd.DataFrame) -> pd.DataFrame:
    """Compare metadata statistics between two merge methods"""
    metadata_desc = {
        'AU': 'Author',
        'DT': 'Document Type',
        'PY': 'Publication Year',
        'TI': 'Title',
        'TC': 'Total Citation',
        'SO': 'Journal',
        'AB': 'Abstract',
        'C1': 'Affiliation',
        'DI': 'DOI',
        'RP': 'Corresponding Author',
        'ID': 'Keywords Plus',
        'WC': 'Science Categories',
        'DE': 'Keywords',
        'LA': 'Language',
        'CR': 'Cited References'
    }
    
    comparison_stats = []
    
    for field, desc in metadata_desc.items():
        if field in simple_df.columns and field in enhanced_df.columns:
            simple_missing = simple_df[field].isna().sum() + simple_df[field].eq('').sum()
            enhanced_missing = enhanced_df[field].isna().sum() + enhanced_df[field].eq('').sum()
            
            simple_missing_pct = (simple_missing / len(simple_df)) * 100
            enhanced_missing_pct = (enhanced_missing / len(enhanced_df)) * 100
            
            # Calculate improvement rate
            improvement = simple_missing_pct - enhanced_missing_pct
            improvement_pct = (improvement / simple_missing_pct * 100) if simple_missing_pct > 0 else 0
            
            # Determine status based on enhanced method results
            if enhanced_missing_pct == 0:
                status = 'Excellent'
            elif enhanced_missing_pct < 1:
                status = 'Very Good'
            elif enhanced_missing_pct < 5:
                status = 'Good'
            elif enhanced_missing_pct < 20:
                status = 'Acceptable'
            elif enhanced_missing_pct < 50:
                status = 'Poor'
            elif enhanced_missing_pct < 90:
                status = 'Critical'
            else:
                status = 'Completely Missing'
            
            comparison_stats.append({
                'Field': field,
                'Description': desc,
                'Simple Missing': simple_missing,
                'Simple Missing %': round(simple_missing_pct, 2),
                'Enhanced Missing': enhanced_missing,
                'Enhanced Missing %': round(enhanced_missing_pct, 2),
                'Improvement': round(improvement, 2),
                'Improvement %': round(improvement_pct, 2),
                'Status': status
            })
    
    return pd.DataFrame(comparison_stats)

def compare_merge_methods(simple_stats: dict, enhanced_stats: dict, 
                        simple_df: pd.DataFrame, enhanced_df: pd.DataFrame) -> dict:
    """Compare two merge methods and provide detailed analysis"""
    
    # 1. Basic Comparison Metrics
    basic_comparison = {
        'Record Counts and Rates': {
            'Simple Merge Records': simple_stats['Total Records'],
            'Enhanced Merge Records': enhanced_stats['Total Records'],
            'Record Difference': enhanced_stats['Total Records'] - simple_stats['Total Records'],
            'Improvement Rate': f"{((enhanced_stats['Total Records'] - simple_stats['Total Records']) / simple_stats['Total Records'] * 100):.2f}%"
        }
    }

    # 2. Data Quality Metrics
    simple_empty = simple_df.isna().sum().sum()
    enhanced_empty = enhanced_df.isna().sum().sum()
    simple_total = len(simple_df) * len(simple_df.columns)
    enhanced_total = len(enhanced_df) * len(enhanced_df.columns)
    
    quality_metrics = {
        'Data Quality Metrics': {
            'Simple Merge Completeness': f"{(1 - simple_empty / simple_total) * 100:.2f}%",
            'Enhanced Merge Completeness': f"{(1 - enhanced_empty / enhanced_total) * 100:.2f}%",
            'Empty Cell Improvement Rate': f"{((simple_empty/simple_total - enhanced_empty/enhanced_total) / (simple_empty/simple_total) * 100):.2f}%",
            'Total Improved Cells': simple_empty - enhanced_empty
        }
    }

    # 3. Field-Based Detailed Analysis
    field_improvements = {}
    for col in enhanced_df.columns:
        if col in simple_df.columns:
            simple_empty = simple_df[col].isna().sum()
            enhanced_empty = enhanced_df[col].isna().sum()
            simple_unique = simple_df[col].nunique()
            enhanced_unique = enhanced_df[col].nunique()
            
            if simple_empty != enhanced_empty or simple_unique != enhanced_unique:
                improvement = {
                    'Simple Empty Cells': simple_empty,
                    'Enhanced Empty Cells': enhanced_empty,
                    'Empty Cell Improvement': simple_empty - enhanced_empty,
                    'Improvement Rate': f"{((simple_empty - enhanced_empty) / simple_empty * 100):.2f}%" if simple_empty > 0 else "0%",
                    'Simple Unique Values': simple_unique,
                    'Enhanced Unique Values': enhanced_unique,
                    'Enrichment': enhanced_unique - simple_unique
                }
                field_improvements[col] = improvement

    return {
        'Basic Comparison': basic_comparison,
        'Data Quality': quality_metrics,
        'Field-Based Improvements': field_improvements
    } 