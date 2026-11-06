"""
Módulo de Identificação Bibliográfica Inteligente e Metadados do Lectoria (Fase 1).
Fornece:
1. Validação, sanitização e conversão de ISBN-10 e ISBN-13.
2. Extração local profunda de metadados em EPUB (OPF completo) e PDF (dicionário interno e páginas 1-4 para CIP/ficha catalográfica).
3. Integração resiliente com Open Library (prioritária) e Google Books (complementar).
4. Cache local em SQLite com TTL e controle estrito de concorrência e rate limiting.
5. Algoritmo de correspondência inteligente (Smart Matching) com pontuação de confiança e distinção entre Obra e Edição.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
import urllib.parse
import urllib.request
import urllib.error

import config
from database import get_db

logger = logging.getLogger("lectoria.metadata")

# Expressões regulares pré-compiladas para identificadores e metadados
ISBN13_REGEX = re.compile(r"(?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx]")
ISBN_SEARCH_REGEX = re.compile(
    r"\b(?:ISBN(?:-1[03])?[\s:]*)?((?:97[89][-\s]?)?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?[\dXx])\b",
    re.IGNORECASE,
)
YEAR_REGEX = re.compile(r"\b(19\d{2}|20\d{2})\b")
CIP_MARKERS = ["ficha catalográfica", "catalogação na publicação", "cip-brasil", "cataloging-in-publication", "dados internacionais de catalogação"]
PUBLISHER_PATTERNS = [
    re.compile(r"(?:editora|edições|editorial|publishing|press|livraria)\s+([A-ZÀ-Ú][a-zà-úA-ZÀ-Ú0-9\s&]+)", re.IGNORECASE),
    re.compile(r"(?:publicado por|published by)\s+([A-ZÀ-Ú][a-zà-úA-ZÀ-Ú0-9\s&]+)", re.IGNORECASE),
]

# Configurações de API e limites
OPENLIBRARY_BASE_URL = "https://openlibrary.org"
OPENLIBRARY_COVERS_BASE = "https://covers.openlibrary.org"
GOOGLE_BOOKS_BASE_URL = "https://www.googleapis.com/books/v1"
USER_AGENT = "Lectoria-Library-Server/1.4.0 (self-hosted; +https://github.com/Nosremekk/Lectoria)"

# Controle de taxa (Rate limiting)
_LAST_OPENLIBRARY_CALL = 0.0
_OPENLIBRARY_MIN_INTERVAL = 1.0  # Mínimo 1 segundo entre chamadas à Open Library


# ==============================================================================
# 1. VALIDAÇÃO E NORMALIZAÇÃO DE ISBN
# ==============================================================================

def clean_isbn(raw_isbn: Optional[str]) -> str:
    """Remove hífens, espaços e caracteres não alfanuméricos de um ISBN."""
    if not raw_isbn:
        return ""
    cleaned = re.sub(r"[^0-9Xx]", "", str(raw_isbn)).upper()
    return cleaned


def validate_isbn10(isbn10: str) -> bool:
    """Valida o dígito verificador de um ISBN-10 (módulo 11)."""
    clean = clean_isbn(isbn10)
    if len(clean) != 10:
        return False
    if not clean[:9].isdigit():
        return False
    
    total = 0
    for idx, char in enumerate(clean[:9]):
        total += int(char) * (10 - idx)
    
    check = 10 if clean[9] == "X" else int(clean[9]) if clean[9].isdigit() else -1
    return (total + check) % 11 == 0


def validate_isbn13(isbn13: str) -> bool:
    """Valida o dígito verificador de um ISBN-13 (módulo 10)."""
    clean = clean_isbn(isbn13)
    if len(clean) != 13 or not clean.isdigit():
        return False
    if not (clean.startswith("978") or clean.startswith("979")):
        return False

    total = 0
    for idx, char in enumerate(clean[:12]):
        weight = 1 if idx % 2 == 0 else 3
        total += int(char) * weight

    check = (10 - (total % 10)) % 10
    return check == int(clean[12])


def isbn10_to_isbn13(isbn10: str) -> Optional[str]:
    """Converte um ISBN-10 válido para ISBN-13 (prefixo 978)."""
    clean = clean_isbn(isbn10)
    if not validate_isbn10(clean):
        return None
    core = "978" + clean[:9]
    total = 0
    for idx, char in enumerate(core):
        weight = 1 if idx % 2 == 0 else 3
        total += int(char) * weight
    check = (10 - (total % 10)) % 10
    return core + str(check)


def format_isbn13(isbn13: str) -> str:
    """Formata um ISBN-13 com hífens legíveis se o tamanho for 13."""
    clean = clean_isbn(isbn13)
    if len(clean) == 13:
        # Formatação padrão genérica: 978-XX-XXXX-XXX-X
        return f"{clean[:3]}-{clean[3:5]}-{clean[5:9]}-{clean[9:12]}-{clean[12]}"
    return clean


def extract_isbns_from_text(text: str) -> List[Dict[str, str]]:
    """Extrai todos os ISBNs válidos (10 ou 13 dígitos) de um texto arbitrário."""
    if not text:
        return []
    
    results = []
    seen = set()

    for match in ISBN_SEARCH_REGEX.finditer(text):
        candidate = clean_isbn(match.group(1))
        if len(candidate) == 13 and validate_isbn13(candidate):
            if candidate not in seen:
                seen.add(candidate)
                results.append({"isbn13": candidate, "isbn10": None, "type": "ISBN-13"})
        elif len(candidate) == 10 and validate_isbn10(candidate):
            as_13 = isbn10_to_isbn13(candidate)
            if candidate not in seen and as_13 not in seen:
                seen.add(candidate)
                if as_13:
                    seen.add(as_13)
                results.append({"isbn13": as_13, "isbn10": candidate, "type": "ISBN-10"})

    return results


# ==============================================================================
# 2. EXTRAÇÃO LOCAL PROFUNDA DE METADADOS (PDF & EPUB)
# ==============================================================================

def normalize_text_for_matching(text: Optional[str]) -> str:
    """Remove diacríticos, pontuação excessiva e converte para minúsculas."""
    if not text:
        return ""
    # Decomposição canônica para separar acentos
    nfkd = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # Remove pontuação, mantém apenas letras, números e espaços
    cleaned = re.sub(r"[^\w\s]", " ", ascii_text).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def calculate_string_similarity(str1: Optional[str], str2: Optional[str]) -> float:
    """
    Calcula similaridade proporcional (0.0 a 1.0) entre duas strings normalizadas.
    Combina sobreposição de conjuntos de palavras (Jaccard) e proporção de caracteres.
    """
    norm1 = normalize_text_for_matching(str1)
    norm2 = normalize_text_for_matching(str2)

    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0

    words1 = set(norm1.split())
    words2 = set(norm2.split())

    if not words1 or not words2:
        return 0.0

    intersection = words1.intersection(words2)
    union = words1.union(words2)
    jaccard = len(intersection) / len(union) if union else 0.0

    # Bônus se uma string for prefixo ou contiver a outra inteira
    substring_bonus = 0.0
    if norm1 in norm2 or norm2 in norm1:
        substring_bonus = 0.25

    return min(1.0, (jaccard * 0.75) + substring_bonus)


def extract_pdf_metadata_advanced(file_path: Path) -> Dict[str, Any]:
    """
    Extrai metadados profundos do PDF:
    - Metadados do cabeçalho (/Title, /Author, /Subject, /Keywords, /CreationDate)
    - Varredura de texto das páginas 1 a 4 em busca de Ficha Catalográfica (CIP),
      ISBNs válidos, editora e ano de publicação.
    """
    import pypdf

    title = None
    author = None
    publisher = None
    publish_year = None
    description = None
    isbn10 = None
    isbn13 = None
    subjects: List[str] = []
    page_count = 0

    try:
        with open(file_path, "rb") as fh:
            reader = pypdf.PdfReader(fh, strict=False)
            page_count = len(reader.pages)

            if reader.metadata:
                raw_title = reader.metadata.get("/Title")
                if raw_title and isinstance(raw_title, str) and raw_title.strip():
                    title = raw_title.strip()

                raw_author = reader.metadata.get("/Author")
                if raw_author and isinstance(raw_author, str) and raw_author.strip():
                    author = raw_author.strip()

                raw_subject = reader.metadata.get("/Subject")
                if raw_subject and isinstance(raw_subject, str) and raw_subject.strip():
                    description = raw_subject.strip()

                raw_keywords = reader.metadata.get("/Keywords")
                if raw_keywords and isinstance(raw_keywords, str):
                    for kw in re.split(r"[,;]", raw_keywords):
                        kw_clean = kw.strip()
                        if kw_clean and kw_clean not in subjects:
                            subjects.append(kw_clean)

                raw_date = reader.metadata.get("/CreationDate")
                if raw_date and isinstance(raw_date, str):
                    m_year = YEAR_REGEX.search(raw_date)
                    if m_year:
                        publish_year = int(m_year.group(1))

            # Varredura de páginas iniciais (1 a 4) para CIP/Ficha catalográfica e ISBN
            max_pages = min(4, page_count)
            extracted_texts = []
            for p_idx in range(max_pages):
                try:
                    p_text = reader.pages[p_idx].extract_text() or ""
                    if p_text:
                        extracted_texts.append(p_text)
                except Exception:
                    pass

            combined_text = "\n".join(extracted_texts)
            if combined_text:
                # 1. Procura ISBNs no texto
                found_isbns = extract_isbns_from_text(combined_text)
                if found_isbns:
                    first = found_isbns[0]
                    isbn13 = first["isbn13"]
                    isbn10 = first["isbn10"]

                # 2. Identifica Ficha Catalográfica (CIP)
                has_cip = any(marker in combined_text.lower() for marker in CIP_MARKERS)
                if has_cip:
                    # Extrai ano do CIP
                    years = YEAR_REGEX.findall(combined_text)
                    if years and not publish_year:
                        publish_year = int(years[-1])

                    # Extrai editora
                    for pub_pat in PUBLISHER_PATTERNS:
                        m_pub = pub_pat.search(combined_text)
                        if m_pub and not publisher:
                            publisher = m_pub.group(1).split("\n")[0].strip()

    except Exception as e:
        logger.debug("Falha na extração de metadados PDF para %s: %s", file_path.name, e)

    # Fallback de título: se o título nos metadados for vazio ou genérico (ex: "Sem título" ou "Microsoft Word")
    generic_titles = {"sem titulo", "sem título", "untitled", "document", "documento", "microsoft word"}
    clean_meta_title = normalize_text_for_matching(title)
    if not title or clean_meta_title in generic_titles:
        title = file_path.stem.replace("_", " ").replace("-", " ").strip()

    return {
        "title": title or file_path.name,
        "author": author,
        "publisher": publisher,
        "publish_year": publish_year,
        "language": None,
        "description": description,
        "isbn10": isbn10,
        "isbn13": isbn13,
        "subjects": subjects,
        "page_count": page_count,
        "format": "pdf",
    }


def extract_epub_metadata_advanced(file_path: Path) -> Dict[str, Any]:
    """
    Extrai metadados completos do pacote EPUB (OPF XML):
    - dc:title, dc:creator, dc:publisher, dc:date, dc:language, dc:description
    - dc:identifier (extrai e valida ISBN-10 e ISBN-13)
    - dc:subject (múltiplas categorias e palavras-chave)
    - Verificação de proteção DRM
    """
    import zipfile
    from epub_service import (
        validate_epub_archive,
        check_epub_drm,
        _get_opf_path,
        _parse_xml_safely,
    )

    validate_epub_archive(file_path)

    title = None
    author = None
    publisher = None
    publish_year = None
    language = None
    description = None
    isbn10 = None
    isbn13 = None
    subjects: List[str] = []
    page_count = 0
    is_drm = False
    drm_type = None

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            is_drm, drm_type = check_epub_drm(zf)
            opf_path = _get_opf_path(zf)
            opf_bytes = zf.read(opf_path)
            root = _parse_xml_safely(opf_bytes)

            for elem in root.iter():
                tag_lower = elem.tag.lower()
                text_val = (elem.text or "").strip()

                if not text_val:
                    if tag_lower.endswith("itemref"):
                        page_count += 1
                    continue

                if tag_lower.endswith("title") and not title:
                    title = text_val
                elif tag_lower.endswith("creator") and not author:
                    author = text_val
                elif tag_lower.endswith("publisher") and not publisher:
                    publisher = text_val
                elif tag_lower.endswith("date") and not publish_year:
                    m_year = YEAR_REGEX.search(text_val)
                    if m_year:
                        publish_year = int(m_year.group(1))
                elif tag_lower.endswith("language") and not language:
                    language = text_val.lower().split("-")[0].strip()
                elif tag_lower.endswith("description") and not description:
                    description = text_val
                elif tag_lower.endswith("subject"):
                    if text_val not in subjects:
                        subjects.append(text_val)
                elif tag_lower.endswith("identifier"):
                    # Analisa possíveis ISBNs
                    clean = clean_isbn(text_val)
                    if len(clean) == 13 and validate_isbn13(clean) and not isbn13:
                        isbn13 = clean
                    elif len(clean) == 10 and validate_isbn10(clean) and not isbn10:
                        isbn10 = clean
                        if not isbn13:
                            isbn13 = isbn10_to_isbn13(clean)

    except Exception as e:
        logger.debug("Falha na extração de metadados EPUB para %s: %s", file_path.name, e)

    if not title:
        title = file_path.stem.replace("_", " ").replace("-", " ").strip()

    return {
        "title": title or file_path.name,
        "author": author,
        "publisher": publisher,
        "publish_year": publish_year,
        "language": language,
        "description": description,
        "isbn10": isbn10,
        "isbn13": isbn13,
        "subjects": subjects,
        "page_count": page_count,
        "format": "epub",
        "is_drm": is_drm,
        "drm_type": drm_type,
    }


# ==============================================================================
# 3. CACHE LOCAL E REQUISIÇÕES HTTP RESILIENTES
# ==============================================================================

def get_cached_api_response(cache_key: str) -> Optional[Dict[str, Any]]:
    """Recupera resposta do cache local se ainda for válida (dentro do TTL)."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT response_json FROM metadata_api_cache
                WHERE cache_key = ? AND expires_at > CURRENT_TIMESTAMP;
                """,
                (cache_key,),
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["response_json"])
    except Exception as e:
        logger.debug("Erro ao consultar cache de API: %s", e)
    return None


def set_cached_api_response(cache_key: str, source: str, data: Dict[str, Any], status_code: int = 200, ttl_days: int = 7) -> None:
    """Armazena resposta de API no cache SQLite local com expiração TTL."""
    try:
        expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(data)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO metadata_api_cache (cache_key, source, response_json, status_code, expires_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (cache_key, source, payload, status_code, expires),
            )
    except Exception as e:
        logger.debug("Erro ao salvar cache de API: %s", e)


def _safe_http_get_json(url: str, cache_key: Optional[str] = None, source: str = "api", timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """
    Executa requisição GET segura para APIs externas:
    - Consulta e preenche o cache local em SQLite.
    - Respeita limites de taxa (rate limiting).
    - Timeout estrito e tratamento de códigos de erro HTTP.
    """
    if cache_key:
        cached = get_cached_api_response(cache_key)
        if cached is not None:
            return cached

    # Rate limiting defensivo para a Open Library
    global _LAST_OPENLIBRARY_CALL
    if "openlibrary.org" in url:
        elapsed = time.time() - _LAST_OPENLIBRARY_CALL
        if elapsed < _OPENLIBRARY_MIN_INTERVAL:
            time.sleep(_OPENLIBRARY_MIN_INTERVAL - elapsed)
        _LAST_OPENLIBRARY_CALL = time.time()

    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if status == 200:
                raw_body = resp.read().decode("utf-8")
                parsed = json.loads(raw_body)
                if cache_key:
                    set_cached_api_response(cache_key, source, parsed, status_code=status)
                return parsed
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug("Falha na consulta HTTP para %s: %s", url, e)
        # Se 404, faz cache de resposta vazia para evitar consultas repetidas
        if isinstance(e, urllib.error.HTTPError) and e.code == 404 and cache_key:
            set_cached_api_response(cache_key, source, {}, status_code=404, ttl_days=3)

    return None


# ==============================================================================
# 4. CLIENTES BIBLIOGRÁFICOS: OPEN LIBRARY & GOOGLE BOOKS
# ==============================================================================

class OpenLibraryClient:
    """Cliente para a API pública da Open Library (Internet Archive)."""

    @staticmethod
    def lookup_isbn(isbn: str) -> Optional[Dict[str, Any]]:
        """Consulta obra e edição por ISBN na Open Library."""
        clean = clean_isbn(isbn)
        if not (validate_isbn13(clean) or validate_isbn10(clean)):
            return None

        cache_key = f"ol:isbn:{clean}"
        url = f"{OPENLIBRARY_BASE_URL}/isbn/{clean}.json"
        data = _safe_http_get_json(url, cache_key=cache_key, source="openlibrary")
        if not data:
            # Tenta via API de livros formatada
            bib_url = f"{OPENLIBRARY_BASE_URL}/api/books?bibkeys=ISBN:{clean}&format=json&jscmd=data"
            bib_data = _safe_http_get_json(bib_url, cache_key=f"ol:bib:{clean}", source="openlibrary")
            if bib_data and f"ISBN:{clean}" in bib_data:
                item = bib_data[f"ISBN:{clean}"]
                return OpenLibraryClient._parse_bib_item(item, clean)
            return None

        return OpenLibraryClient._parse_edition_json(data, clean)

    @staticmethod
    def search(title: str, author: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Busca livros por título e autor na Open Library."""
        query_params = {"title": title, "limit": limit}
        if author:
            query_params["author"] = author

        query_str = urllib.parse.urlencode(query_params)
        cache_key = f"ol:search:{query_str}"
        url = f"{OPENLIBRARY_BASE_URL}/search.json?{query_str}"

        data = _safe_http_get_json(url, cache_key=cache_key, source="openlibrary")
        if not data or "docs" not in data:
            return []

        results = []
        for doc in data.get("docs", [])[:limit]:
            parsed = OpenLibraryClient._parse_search_doc(doc)
            if parsed:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_edition_json(data: Dict[str, Any], searched_isbn: str) -> Dict[str, Any]:
        """Normaliza o JSON retornado por /isbn/{isbn}.json."""
        # Título
        title = data.get("title", "")
        
        # Autores (podem ser referências chave {"key": "/authors/OL..."})
        authors = []
        for a in data.get("authors", []):
            if isinstance(a, dict):
                if "name" in a and a["name"]:
                    authors.append(a["name"])
                elif "key" in a:
                    pass
            elif isinstance(a, str):
                authors.append(a)
        author = ", ".join(authors) if authors else None

        # Ano
        publish_year = None
        date_str = data.get("publish_date", "")
        m_year = YEAR_REGEX.search(date_str)
        if m_year:
            publish_year = int(m_year.group(1))

        # Editora
        publishers = data.get("publishers", [])
        publisher = publishers[0] if publishers else None

        # ISBNs
        isbn10_list = data.get("isbn_10", [])
        isbn13_list = data.get("isbn_13", [])
        isbn10 = isbn10_list[0] if isbn10_list else None
        isbn13 = isbn13_list[0] if isbn13_list else (searched_isbn if len(searched_isbn) == 13 else None)

        # Capa
        cover_url = None
        covers = data.get("covers", [])
        if covers and covers[0] > 0:
            cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/id/{covers[0]}-L.jpg"
        elif isbn13:
            cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/isbn/{isbn13}-L.jpg"

        # Obra (work_id)
        works = data.get("works", [])
        work_id = works[0].get("key", "").split("/")[-1] if works else None

        return {
            "source": "openlibrary",
            "external_id": data.get("key", "").split("/")[-1] or searched_isbn,
            "work_id": work_id,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_year": publish_year,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": [],
            "raw_data": data,
        }

    @staticmethod
    def _parse_bib_item(item: Dict[str, Any], searched_isbn: str) -> Dict[str, Any]:
        """Normaliza o JSON retornado por /api/books?jscmd=data."""
        title = item.get("title", "")
        authors = [a.get("name") for a in item.get("authors", []) if "name" in a]
        author = ", ".join(authors) if authors else None

        publish_year = None
        m_year = YEAR_REGEX.search(item.get("publish_date", ""))
        if m_year:
            publish_year = int(m_year.group(1))

        publishers = [p.get("name") for p in item.get("publishers", []) if "name" in p]
        publisher = publishers[0] if publishers else None

        subjects = [s.get("name") for s in item.get("subjects", []) if "name" in s]

        cover_url = None
        if "cover" in item and "large" in item["cover"]:
            cover_url = item["cover"]["large"]
        elif "cover" in item and "medium" in item["cover"]:
            cover_url = item["cover"]["medium"]

        identifiers = item.get("identifiers", {})
        isbn10 = identifiers.get("isbn_10", [None])[0]
        isbn13 = identifiers.get("isbn_13", [None])[0] or (searched_isbn if len(searched_isbn) == 13 else None)

        return {
            "source": "openlibrary",
            "external_id": searched_isbn,
            "work_id": None,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_year": publish_year,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": subjects,
            "raw_data": item,
        }

    @staticmethod
    def _parse_search_doc(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normaliza um documento retornado por /search.json."""
        title = doc.get("title")
        if not title:
            return None

        authors = doc.get("author_name", [])
        author = ", ".join(authors) if authors else None

        first_publish_year = doc.get("first_publish_year")
        publishers = doc.get("publisher", [])
        publisher = publishers[0] if publishers else None

        isbns = doc.get("isbn", [])
        isbn10 = None
        isbn13 = None
        for cand in isbns:
            clean = clean_isbn(cand)
            if len(clean) == 13 and not isbn13:
                isbn13 = clean
            elif len(clean) == 10 and not isbn10:
                isbn10 = clean

        cover_id = doc.get("cover_i")
        cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/id/{cover_id}-L.jpg" if cover_id else None

        work_key = doc.get("key", "").split("/")[-1]
        subjects = doc.get("subject", [])[:10]

        return {
            "source": "openlibrary",
            "external_id": work_key or (isbn13 or isbn10 or title),
            "work_id": work_key,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_year": first_publish_year,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": subjects,
            "raw_data": doc,
        }


class GoogleBooksClient:
    """Cliente para a Google Books API (fonte complementar)."""

    @staticmethod
    def lookup_isbn(isbn: str) -> Optional[Dict[str, Any]]:
        """Consulta por ISBN na Google Books API."""
        clean = clean_isbn(isbn)
        if not (validate_isbn13(clean) or validate_isbn10(clean)):
            return None

        cache_key = f"gb:isbn:{clean}"
        url = f"{GOOGLE_BOOKS_BASE_URL}/volumes?q=isbn:{clean}&maxResults=3"
        data = _safe_http_get_json(url, cache_key=cache_key, source="googlebooks")
        if not data or "items" not in data or not data["items"]:
            return None

        return GoogleBooksClient._parse_volume(data["items"][0])

    @staticmethod
    def search(title: str, author: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Busca livros por título e autor na Google Books API."""
        query_parts = [f"intitle:{title}"]
        if author:
            query_parts.append(f"inauthor:{author}")

        query_str = "+".join(query_parts)
        encoded_query = urllib.parse.quote(query_str, safe="+:")
        cache_key = f"gb:search:{encoded_query}:{limit}"
        url = f"{GOOGLE_BOOKS_BASE_URL}/volumes?q={encoded_query}&maxResults={limit}"

        data = _safe_http_get_json(url, cache_key=cache_key, source="googlebooks")
        if not data or "items" not in data:
            return []

        results = []
        for item in data.get("items", []):
            parsed = GoogleBooksClient._parse_volume(item)
            if parsed:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_volume(volume: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normaliza um volume retornado pela Google Books API."""
        info = volume.get("volumeInfo", {})
        title = info.get("title")
        if not title:
            return None

        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else None

        publish_year = None
        m_year = YEAR_REGEX.search(info.get("publishedDate", ""))
        if m_year:
            publish_year = int(m_year.group(1))

        publisher = info.get("publisher")
        description = info.get("description")
        language = info.get("language")

        isbn10 = None
        isbn13 = None
        for ident in info.get("industryIdentifiers", []):
            i_type = ident.get("type", "")
            i_val = clean_isbn(ident.get("identifier", ""))
            if i_type == "ISBN_13" and validate_isbn13(i_val):
                isbn13 = i_val
            elif i_type == "ISBN_10" and validate_isbn10(i_val):
                isbn10 = i_val

        # Capa com protocolo HTTPS seguro e remoção de restrições de zoom
        cover_url = None
        images = info.get("imageLinks", {})
        raw_cover = images.get("extraLarge") or images.get("large") or images.get("medium") or images.get("thumbnail")
        if raw_cover:
            cover_url = raw_cover.replace("http://", "https://")

        subjects = info.get("categories", [])

        return {
            "source": "googlebooks",
            "external_id": volume.get("id"),
            "work_id": None,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_year": publish_year,
            "language": language,
            "description": description,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": subjects,
            "raw_data": volume,
        }


# ==============================================================================
# 5. CORRESPONDÊNCIA INTELIGENTE (SMART MATCHER)
# ==============================================================================

def score_candidate(book_meta: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[float, str]:
    """
    Atribui uma pontuação ponderada de confiança (0.0 a 100.0) a um candidato bibliográfico:
    - Correspondência exata de ISBN (ISBN-13 ou ISBN-10): +55 pontos
    - Similaridade de título (normalizado): até +30 pontos
    - Similaridade de autor: até +15 pontos
    - Coincidência de ano ou editora: até +5 pontos

    Níveis de confiança retornados:
    - 'reliable'      (Score >= 85.0): Correspondência segura
    - 'needs_review'  (50.0 <= Score < 85.0): Exige revisão manual
    - 'insufficient'  (Score < 50.0): Insuficiente para enriquecimento automático
    """
    score = 0.0

    # 1. Checagem de ISBN
    book_isbn13 = clean_isbn(book_meta.get("isbn13"))
    book_isbn10 = clean_isbn(book_meta.get("isbn10"))
    cand_isbn13 = clean_isbn(candidate.get("isbn13"))
    cand_isbn10 = clean_isbn(candidate.get("isbn10"))

    isbn_matched = False
    if book_isbn13 and cand_isbn13 and book_isbn13 == cand_isbn13:
        score += 55.0
        isbn_matched = True
    elif book_isbn10 and cand_isbn10 and book_isbn10 == cand_isbn10:
        score += 55.0
        isbn_matched = True

    # 2. Similaridade de Título
    title_sim = calculate_string_similarity(book_meta.get("title"), candidate.get("title"))
    score += title_sim * 30.0

    # 3. Similaridade de Autor
    author_sim = 0.0
    if book_meta.get("author") and candidate.get("author"):
        author_sim = calculate_string_similarity(book_meta.get("author"), candidate.get("author"))
        score += author_sim * 20.0
    elif isbn_matched:
        # Se ISBN conferiu mas o livro local não tinha autor registrado, bonifica proporcionalmente
        score += 10.0

    # Bônus para forte correlação de Título + Autor quando não há ISBN disponível
    if not isbn_matched and title_sim >= 0.5 and author_sim >= 0.5:
        score += ((title_sim + author_sim) / 2.0) * 30.0

    # 4. Ano de Publicação
    book_year = book_meta.get("publish_year")
    cand_year = candidate.get("publish_year")
    if book_year and cand_year:
        if abs(book_year - cand_year) <= 1:
            score += 3.0
        elif abs(book_year - cand_year) <= 5:
            score += 1.5

    # 5. Editora
    book_pub = book_meta.get("publisher")
    cand_pub = candidate.get("publisher")
    if book_pub and cand_pub:
        pub_sim = calculate_string_similarity(book_pub, cand_pub)
        score += pub_sim * 2.0

    final_score = round(min(100.0, score), 1)

    if final_score >= 85.0:
        confidence = "reliable"
    elif final_score >= 50.0:
        confidence = "needs_review"
    else:
        confidence = "insufficient"

    return final_score, confidence


def identify_book_metadata(book_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Executa estratégia completa de busca e identificação bibliográfica:
    1. Consulta por ISBN na Open Library (prioritária).
    2. Se não houver resultado satisfatório, consulta por ISBN no Google Books.
    3. Se não houver ISBN ou não retornar correspondência confiável, busca por Título + Autor.
    4. Avalia e pontua todos os candidatos encontrados.
    Retorna lista ordenada de candidatos por pontuação decrescente.
    """
    candidates = []
    seen_ids = set()

    isbn13 = book_meta.get("isbn13")
    isbn10 = book_meta.get("isbn10")
    primary_isbn = isbn13 or isbn10

    # 1. Consulta por ISBN
    if primary_isbn:
        ol_res = OpenLibraryClient.lookup_isbn(primary_isbn)
        if ol_res:
            score, conf = score_candidate(book_meta, ol_res)
            ol_res["score"] = score
            ol_res["confidence_level"] = conf
            candidates.append(ol_res)
            seen_ids.add(ol_res["external_id"])

        gb_res = GoogleBooksClient.lookup_isbn(primary_isbn)
        if gb_res and gb_res["external_id"] not in seen_ids:
            score, conf = score_candidate(book_meta, gb_res)
            gb_res["score"] = score
            gb_res["confidence_level"] = conf
            candidates.append(gb_res)
            seen_ids.add(gb_res["external_id"])

    # Se já encontrou candidato seguro via ISBN (score >= 85), não precisa de buscas textuais difusas
    if candidates and candidates[0]["score"] >= 85.0:
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    # 2. Busca por Título e Autor
    title = book_meta.get("title")
    author = book_meta.get("author")

    if title:
        # Open Library Search
        ol_results = OpenLibraryClient.search(title, author=author, limit=4)
        for cand in ol_results:
            cid = cand.get("external_id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                score, conf = score_candidate(book_meta, cand)
                cand["score"] = score
                cand["confidence_level"] = conf
                candidates.append(cand)

        # Google Books Search
        gb_results = GoogleBooksClient.search(title, author=author, limit=4)
        for cand in gb_results:
            cid = cand.get("external_id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                score, conf = score_candidate(book_meta, cand)
                cand["score"] = score
                cand["confidence_level"] = conf
                candidates.append(cand)

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates
