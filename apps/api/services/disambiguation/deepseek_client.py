"""Minimal DeepSeek API client using OpenAI-compatible chat completions.

Provides a thin wrapper around the chat completions endpoint with helpers for
JSON-mode responses, free-form text responses, and async variants.
"""

from __future__ import annotations

import json
import asyncio
from typing import Any

import requests


class DeepSeekError(Exception):
    pass


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com", model: str = "deepseek-v4-flash"):
        if not api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY is not set")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if not r.ok:
            raise DeepSeekError(f"DeepSeek {r.status_code}: {r.text[:200]}")
        return r.json()

    def chat_json(self, system: str, user_payload: dict, temperature: float = 0.0) -> dict:
        """JSON yanıt iste. Prompt 'json' kelimesini içermek zorunda (DeepSeek kuralı)."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        }
        data = self._post(body)
        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise DeepSeekError(f"Could not parse response: {e}; raw={data}")

    async def chat_json_async(self, system: str, user_payload: dict, temperature: float = 0.0) -> dict:
        return await asyncio.to_thread(self.chat_json, system, user_payload, temperature)

    def chat_text(self, system: str, user_text: str, temperature: float = 0.3) -> str:
        """Serbest metin yanıt iste (JSON zorlaması yok). Metodoloji raporu gibi
        düz prose çıktılar için kullanılır."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": temperature,
        }
        data = self._post(body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise DeepSeekError(f"Could not parse response: {e}; raw={data}")

    async def chat_text_async(self, system: str, user_text: str, temperature: float = 0.3) -> str:
        return await asyncio.to_thread(self.chat_text, system, user_text, temperature)


AUTHOR_SYSTEM_PROMPT = """You are a bibliometric data-quality expert. Your task: review the candidate author set provided by the user and determine which variants belong to the SAME person.

Signals to use when deciding:
- Name variants (initial variation, transliteration, married surname)
- Overlapping affiliations or known mobility (change of university)
- Shared coauthors (strong signal)
- Consistency of research field
- Chronological consistency of the year range

Return the answer in this JSON schema:
{
  "clusters": [
    {
      "cluster_id": "c1",
      "member_ids": ["v1", "v3"],   (use the EXACT candidate "id" values provided, e.g. "v1","v2"; do NOT invent new labels)
      "confidence": 0.0-1.0,
      "reason": "short explanation (English)"
    }
  ],
  "uncertain": [
    {"id": "z", "reason": "why it is uncertain (English)"}
  ]
}

Only place decisions with confidence>=0.7 into the same cluster. List uncertain ones in the "uncertain" list.
Respond with JSON only, write no other text."""

AUTHOR_SPLIT_SYSTEM_PROMPT = """You are a bibliometric data-quality expert. Your task: decide whether a SINGLE author name spelling (e.g., "Mehmet A") should be split into record groups belonging to different research fields.

Question: Do these records belong to DIFFERENT people sharing the SAME name (split), or to a SINGLE person working across disciplines (keep)?

Signals:
- If the research fields/categories are completely far apart (e.g., pure Physics vs pure Literature), the probability of different people is high -> split.
- If the fields are neighboring/related or there is an interdisciplinary topic, it may be a single person -> keep.
- Some records may lack field information; take this uncertainty into account.

Return the answer in this JSON schema:
{
  "decision": "split" | "keep",
  "confidence": 0.0-1.0,
  "reason": "short explanation (English)"
}
Respond with JSON only, write no other text."""

AFFILIATION_SYSTEM_PROMPT = """You are an institution-name normalization expert. Your task: determine which of the given affiliation variants belong to the SAME institution.

Even if the institutional hierarchy (university > faculty > department) looks different, variants may be in the same cluster when they share the same parent institution; however, separate campuses/cities belong to different clusters.

Return the answer in this JSON schema:
{
  "clusters": [
    {
      "cluster_id": "c1",
      "member_ids": ["v1", "v3"],   (use the EXACT candidate "id" values provided, e.g. "v1","v2"; do NOT invent new labels)
      "canonical_name": "Suggested standard spelling (official English form)",
      "country": "ISO 3166-1 alpha-2 or empty",
      "confidence": 0.0-1.0,
      "reason": "short explanation (English)"
    }
  ],
  "uncertain": [
    {"id": "z", "reason": "..."}
  ]
}

Respond with JSON only, write no other text."""


METHODOLOGY_SYSTEM_PROMPT = """You are an academic writing assistant. You help researchers draft the "Data Collection and Preparation" (Methodology) subsection of a bibliometric study.

You will receive a chronological JSON log of operations the researcher performed in BibexPy, a tool that merges, cleans, filters, enriches and disambiguates bibliographic records exported from Web of Science (WoS) and Scopus.

Write a clear, formal methodology narrative IN ENGLISH describing the data preparation workflow. Requirements:
- Use academic past tense and passive voice (e.g., "A total of N records were retrieved from...", "After deduplication, M unique records remained...", "Records published before YYYY were excluded...").
- Report the SPECIFIC numbers that appear in the log (input counts, merged/unique counts, removed duplicates, excluded/filtered records, enriched fields, normalized/split author names).
- Follow the actual chronological order of the steps as logged.
- Name the source databases (Web of Science and/or Scopus) and state that data preparation was performed using BibexPy, with an APA-style in-text citation "(Kara et al., 2025)".
- When duplicate removal or record linkage is described, you may note that it followed established record-linkage practice; when author name handling is described, refer to it as author name disambiguation (normalization of name variants and separation of homonymous names).
- Do NOT invent steps, numbers, criteria, or external citations that are not present in the log — the ONLY citation you must add is the BibexPy reference specified below. If a detail is missing, omit it rather than fabricating.
- Be concise and publication-ready: one to a few short paragraphs. You MAY use brief Markdown subheadings (e.g., "## Data sources", "## Deduplication and merging", "## Screening and enrichment", "## Author disambiguation") if it improves clarity.
- End with a short "## Reference" section that contains EXACTLY this reference (do not alter it): Kara, B. C., Şahin, A., & Dirsehan, T. (2025). BibexPy: Harmonizing the bibliometric symphony of Scopus and Web of Science. SoftwareX, 30, 102098. https://doi.org/10.1016/j.softx.2025.102098

Output Markdown text only. Do not add a preamble like "Here is the methodology"; start directly with the content."""


COUNTRY_SYSTEM_PROMPT = """You are a bibliometric data-quality expert. You receive tokens taken from the LAST component of author addresses (the C1 field) that did NOT match the automatic country dictionary.

Some tokens ARE country-name spellings the dictionary missed (abbreviation, typo, local or former name, e.g. "U.S.A.", "Peoples R China", "Deutschland") — group THESE by the country they denote.

Other tokens are NOT countries at all (institutions, cities, fragments, e.g. "Michigan State University", "Hangzhou") — put EACH of these in "uncertain". NEVER cluster a non-country token as a country and NEVER map an institution/city to a country here. (Missing countries are completed separately from the API, not guessed here.)

Return the answer in this JSON schema:
{
  "clusters": [
    {
      "cluster_id": "c1",
      "member_ids": ["v1", "v3"],   (use the EXACT candidate "id" values provided, e.g. "v1","v2"; do NOT invent new labels)
      "canonical_name": "Canonical English country name (ISO 3166, e.g., United States)",
      "confidence": 0.0-1.0,
      "reason": "short explanation (English)"
    }
  ],
  "uncertain": [ {"id": "z", "reason": "not a country (e.g., institution / city / fragment)"} ]
}
Respond with JSON only, write no other text."""


ORG_ROLLUP_SYSTEM_PROMPT = """You are a bibliometric data-quality expert. Your task: group the given FULL address (affiliation) strings by the PARENT INSTITUTION (university / research institution) they belong to.

Goal: build the collaboration network through the PARENT INSTITUTION rather than the department/sub-unit. Different departments or spelling variants of the same parent institution should be in a single cluster; DIFFERENT campuses/cities or different institutions must stay in SEPARATE clusters.

canonical_name: the standard name of the parent institution — WITHOUT department and geography (e.g., "University of Oxford").

Return the answer in this JSON schema:
{
  "clusters": [
    {
      "cluster_id": "c1",
      "member_ids": ["v1", "v3"],   (use the EXACT candidate "id" values provided, e.g. "v1","v2"; do NOT invent new labels)
      "canonical_name": "Parent institution standard name",
      "confidence": 0.0-1.0,
      "reason": "short explanation (English)"
    }
  ],
  "uncertain": [ {"id": "z", "reason": "why uncertain (English)"} ]
}
Respond with JSON only, write no other text."""
