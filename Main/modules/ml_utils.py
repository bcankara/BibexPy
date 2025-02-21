import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multioutput import MultiOutputClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import MultiLabelBinarizer
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import re
from colorama import Fore, Style

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('punkt')
    nltk.download('stopwords')

def preprocess_text(text):
    """Preprocess text data for ML"""
    if pd.isna(text):
        return ""
    
    # Convert to string if not already
    text = str(text)
    
    # Convert to lowercase
    text = text.lower()
    
    # Remove special characters and digits
    text = re.sub(r'[^a-zA-Z\s]', ' ', text)
    
    # Remove extra whitespace
    text = ' '.join(text.split())
    
    # Tokenization
    tokens = word_tokenize(text)
    
    # Remove stopwords
    stop_words = set(stopwords.words('english'))
    tokens = [token for token in tokens if token not in stop_words]
    
    return ' '.join(tokens)

def prepare_training_data(df, text_columns=['TI', 'AB']):
    """Prepare text data for training"""
    # Combine and preprocess text columns
    combined_text = df[text_columns].fillna('').agg(' '.join, axis=1)
    processed_text = combined_text.apply(preprocess_text)
    
    return processed_text

def train_keyword_model(train_df, text_columns=['TI', 'AB']):
    """Train model for keyword prediction"""
    # Prepare text data
    X = prepare_training_data(train_df, text_columns)
    
    # Prepare keywords data
    mlb = MultiLabelBinarizer()
    keywords = train_df['DE'].fillna('').str.split(';')
    keywords = [[kw.strip() for kw in kws if kw.strip()] for kws in keywords]
    y = mlb.fit_transform(keywords)
    
    # Create and train TF-IDF vectorizer
    vectorizer = TfidfVectorizer(max_features=5000)
    X_tfidf = vectorizer.fit_transform(X)
    
    # Train model
    model = MultiOutputClassifier(RandomForestClassifier(n_estimators=100))
    model.fit(X_tfidf, y)
    
    return vectorizer, model, mlb

def train_subject_model(train_df, text_columns=['TI', 'AB']):
    """Train model for subject category prediction"""
    # Prepare text data
    X = prepare_training_data(train_df, text_columns)
    
    # Prepare subject categories data
    mlb = MultiLabelBinarizer()
    subjects = train_df['SC'].fillna('').str.split(';')
    subjects = [[subj.strip() for subj in subjs if subj.strip()] for subjs in subjects]
    y = mlb.fit_transform(subjects)
    
    # Create and train TF-IDF vectorizer
    vectorizer = TfidfVectorizer(max_features=5000)
    X_tfidf = vectorizer.fit_transform(X)
    
    # Train model
    model = MultiOutputClassifier(RandomForestClassifier(n_estimators=100))
    model.fit(X_tfidf, y)
    
    return vectorizer, model, mlb

def predict_keywords(text, vectorizer, model, mlb, threshold=0.3):
    """Predict keywords for given text"""
    # Preprocess text
    processed_text = preprocess_text(text)
    
    # Transform text using vectorizer
    X_tfidf = vectorizer.transform([processed_text])
    
    # Predict probabilities
    y_pred_proba = model.predict_proba(X_tfidf)
    
    # Get keywords above threshold
    keywords = []
    for i, proba in enumerate(zip(*[estimator.predict_proba(X_tfidf)[0] for estimator in model.estimators_])):
        if max(proba) >= threshold:
            keywords.append(mlb.classes_[i])
    
    return '; '.join(keywords) if keywords else None

def predict_subjects(text, vectorizer, model, mlb, threshold=0.3):
    """Predict subject categories for given text"""
    # Preprocess text
    processed_text = preprocess_text(text)
    
    # Transform text using vectorizer
    X_tfidf = vectorizer.transform([processed_text])
    
    # Predict probabilities
    y_pred_proba = model.predict_proba(X_tfidf)
    
    # Get subjects above threshold
    subjects = []
    for i, proba in enumerate(zip(*[estimator.predict_proba(X_tfidf)[0] for estimator in model.estimators_])):
        if max(proba) >= threshold:
            subjects.append(mlb.classes_[i])
    
    return '; '.join(subjects) if subjects else None

def train_keywords_plus_model(train_df, text_columns=['TI', 'AB']):
    """Train model for Keywords Plus prediction"""
    # Prepare text data
    X = prepare_training_data(train_df, text_columns)
    
    # Prepare Keywords Plus data
    mlb = MultiLabelBinarizer()
    keywords_plus = train_df['ID'].fillna('').str.split(';')
    keywords_plus = [[kw.strip() for kw in kws if kw.strip()] for kws in keywords_plus]
    y = mlb.fit_transform(keywords_plus)
    
    # Create and train TF-IDF vectorizer
    vectorizer = TfidfVectorizer(max_features=5000)
    X_tfidf = vectorizer.fit_transform(X)
    
    # Train model
    model = MultiOutputClassifier(RandomForestClassifier(n_estimators=100))
    model.fit(X_tfidf, y)
    
    return vectorizer, model, mlb

def predict_keywords_plus(text, vectorizer, model, mlb, threshold=0.3):
    """Predict Keywords Plus for given text"""
    # Preprocess text
    processed_text = preprocess_text(text)
    
    # Transform text using vectorizer
    X_tfidf = vectorizer.transform([processed_text])
    
    # Predict probabilities
    y_pred_proba = model.predict_proba(X_tfidf)
    
    # Get keywords above threshold
    keywords_plus = []
    for i, proba in enumerate(zip(*[estimator.predict_proba(X_tfidf)[0] for estimator in model.estimators_])):
        if max(proba) >= threshold:
            keywords_plus.append(mlb.classes_[i])
    
    return '; '.join(keywords_plus) if keywords_plus else None

def enrich_metadata_ml(df):
    """Enrich metadata using ML models"""
    print("\nStarting ML-based metadata enrichment...")
    
    # Create copies of the dataframe for training
    train_df_keywords = df[df['DE'].notna()].copy()
    train_df_keywords_plus = df[df['ID'].notna()].copy()  # New training set for Keywords Plus
    train_df_subjects = df[df['SC'].notna()].copy()
    
    # Create a copy of the input dataframe
    enriched_df = df.copy()
    
    # Initialize counters for ID field
    original_empty_id = df['ID'].isna().sum()
    
    # Train models if enough training data is available
    if len(train_df_keywords) > 10:
        print(f"\n{Fore.CYAN}Training keyword prediction model...{Style.RESET_ALL}")
        print(f"Using {len(train_df_keywords)} records for training")
        kw_vectorizer, kw_model, kw_mlb = train_keyword_model(train_df_keywords)
        
        # Predict missing keywords
        print(f"\n{Fore.CYAN}Predicting missing keywords...{Style.RESET_ALL}")
        mask = enriched_df['DE'].isna()
        total = mask.sum()
        completed = 0
        
        for idx in enriched_df[mask].index:
            text = ' '.join([
                str(enriched_df.loc[idx, 'TI']) if pd.notna(enriched_df.loc[idx, 'TI']) else '',
                str(enriched_df.loc[idx, 'AB']) if pd.notna(enriched_df.loc[idx, 'AB']) else ''
            ])
            if text.strip():
                predicted_keywords = predict_keywords(text, kw_vectorizer, kw_model, kw_mlb)
                if predicted_keywords:
                    enriched_df.loc[idx, 'DE'] = predicted_keywords
            completed += 1
            if completed % 10 == 0:
                print(f"Progress: {completed}/{total} records processed", end='\r')
        print()  # New line after progress
    
    # Train Keywords Plus model if enough training data is available
    if len(train_df_keywords_plus) > 10:
        print(f"\n{Fore.CYAN}Training Keywords Plus prediction model...{Style.RESET_ALL}")
        print(f"Using {len(train_df_keywords_plus)} records for training")
        kw_plus_vectorizer, kw_plus_model, kw_plus_mlb = train_keywords_plus_model(train_df_keywords_plus)
        
        # Predict missing Keywords Plus
        print(f"\n{Fore.CYAN}Predicting missing Keywords Plus...{Style.RESET_ALL}")
        mask = enriched_df['ID'].isna()
        total = mask.sum()
        completed = 0
        
        for idx in enriched_df[mask].index:
            text = ' '.join([
                str(enriched_df.loc[idx, 'TI']) if pd.notna(enriched_df.loc[idx, 'TI']) else '',
                str(enriched_df.loc[idx, 'AB']) if pd.notna(enriched_df.loc[idx, 'AB']) else ''
            ])
            if text.strip():
                predicted_keywords_plus = predict_keywords_plus(text, kw_plus_vectorizer, kw_plus_model, kw_plus_mlb)
                if predicted_keywords_plus:
                    enriched_df.loc[idx, 'ID'] = predicted_keywords_plus
            completed += 1
            if completed % 10 == 0:
                print(f"Progress: {completed}/{total} records processed", end='\r')
        print()  # New line after progress
    
    # Train models if enough training data is available
    if len(train_df_subjects) > 10:
        print(f"\n{Fore.CYAN}Training subject category prediction model...{Style.RESET_ALL}")
        print(f"Using {len(train_df_subjects)} records for training")
        subj_vectorizer, subj_model, subj_mlb = train_subject_model(train_df_subjects)
        
        # Predict missing subject categories
        print(f"\n{Fore.CYAN}Predicting missing subject categories...{Style.RESET_ALL}")
        mask = enriched_df['SC'].isna()
        total = mask.sum()
        completed = 0
        
        for idx in enriched_df[mask].index:
            text = ' '.join([
                str(enriched_df.loc[idx, 'TI']) if pd.notna(enriched_df.loc[idx, 'TI']) else '',
                str(enriched_df.loc[idx, 'AB']) if pd.notna(enriched_df.loc[idx, 'AB']) else ''
            ])
            if text.strip():
                predicted_subjects = predict_subjects(text, subj_vectorizer, subj_model, subj_mlb)
                if predicted_subjects:
                    enriched_df.loc[idx, 'SC'] = predicted_subjects
                    enriched_df.loc[idx, 'WC'] = predicted_subjects  # Copy to WC as well
            completed += 1
            if completed % 10 == 0:
                print(f"Progress: {completed}/{total} records processed", end='\r')
        print()  # New line after progress
    
    # Calculate enrichment statistics
    stats = {
        'total_records': len(df),
        'original_empty_keywords': df['DE'].isna().sum(),
        'original_empty_subjects': df['SC'].isna().sum(),
        'original_empty_id': original_empty_id,
        'enriched_empty_keywords': enriched_df['DE'].isna().sum(),
        'enriched_empty_subjects': enriched_df['SC'].isna().sum(),
        'enriched_empty_id': enriched_df['ID'].isna().sum(),
        'keywords_filled': df['DE'].isna().sum() - enriched_df['DE'].isna().sum(),
        'subjects_filled': df['SC'].isna().sum() - enriched_df['SC'].isna().sum(),
        'id_filled': original_empty_id - enriched_df['ID'].isna().sum()
    }
    
    return enriched_df, stats 