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
ISBN13_REGEX = re.compile(r"(?:97[89][-\s\u2010-\u2015\xad]?)?(?:\d[-\s\u2010-\u2015\xad]?){9}[\dXx]")
ISBN_SEARCH_REGEX = re.compile(
    r"(?:ISBN(?:-1[03])?[\s:]*|(?<!\d))((?:97[89][-\s\u2010-\u2015\xad]?)?(?:\d[-\s\u2010-\u2015\xad]?){9}[\dXx])(?!\d)",
    re.IGNORECASE,
)
YEAR_REGEX = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
CIP_MARKERS = [
    "ficha catalográfica", "ficha catalografica",
    "catalogação na publicação", "catalogacao na publicacao",
    "cip-brasil", "cataloging-in-publication",
    "dados internacionais de catalogação", "dados internacionais de catalogacao",
    "câmara brasileira do livro", "camara brasileira do livro",
    "sindicato nacional dos editores",
]
PUBLISHER_PATTERNS = [
    re.compile(r"(?:editora|edições|edicoes|editorial|publishing|press|livraria)\s+([A-ZÀ-Ú][a-zà-úA-ZÀ-Ú0-9\s&\-\.]{1,40})", re.IGNORECASE),
    re.compile(r"(?:publicado por|published by)\s+([A-ZÀ-Ú][a-zà-úA-ZÀ-Ú0-9\s&\-\.]{1,40})", re.IGNORECASE),
    re.compile(r":\s*([A-ZÀ-Ú][a-zà-úA-ZÀ-Ú\s&]{2,30}),\s*(?:19\d{2}|20[0-2]\d)\b"),
]

# Padrões de ruído comuns em arquivos baixados ou digitalizados
NOISE_BRACKET_PATTERNS = [
    re.compile(r"\s*[\(\[\{][^)\]\}]*(?:z-lib|z-library|libgen|pdfdrive|annas-archive|ebiblioteca|lelivros|docero|scihub|sci-hub|digitalizado|scanned|ocr|www\.|http|\.org|\.com|\.net)[^)\]\}]*[\)\]\}]", re.IGNORECASE),
    re.compile(r"\s*[\(\[\{](?:pdf|epub|mobi|azw3|pt-br|pt_br|portugues|português|inglês|english|scan|v\d+)[\)\]\}]", re.IGNORECASE),
    re.compile(r"\s*\(\d+\)\s*$"),
]
VOLUME_REGEX = re.compile(
    r"\b(?:vol(?:ume)?\.?\s*(\d+|[IVX]+)|v\.?\s*(\d+)|tomo\s*(\d+|[IVX]+))\b",
    re.IGNORECASE,
)
EDITION_REGEX = re.compile(
    r"\b(\d+\s*[ªºa°]?\s*ed(?:i[çc][ãa]o|\.)?|\d+(?:st|nd|rd|th)\s+ed(?:ition|\.)?|edi[çc][ãa]o\s+[\wÀ-ú]+)\b",
    re.IGNORECASE,
)
GENERIC_TITLE_PATTERNS = re.compile(
    r"^(?:sem\s+t[ií]tulo|untitled|document\d*|documento\d*|microsoft\s+word.*|slide\s*\d*|camscanner.*|scan\d*|binder\d*|apresenta[çc][ãa]o\d*|livro\d*|cap[ií]tulo\s*\d+|page\s*\d+|\d+[\s_-]*iniciais.*)$",
    re.IGNORECASE,
)
GENERIC_FOLDER_NAMES = {
    "books", "livros", "biblioteca", "library", "pdf", "pdfs", "epub", "epubs",
    "downloads", "download", "documentos", "documents", "acervo", "geral", "tmp", "test",
}

# Configurações de API e limites
OPENLIBRARY_BASE_URL = "https://openlibrary.org"
OPENLIBRARY_COVERS_BASE = "https://covers.openlibrary.org"
GOOGLE_BOOKS_BASE_URL = "https://www.googleapis.com/books/v1"
CROSSREF_BASE_URL = "https://api.crossref.org"
BRASILAPI_BASE_URL = "https://brasilapi.com.br/api/isbn/v1"
USER_AGENT = "Lectoria-Library-Server/1.5.2 (self-hosted; +https://github.com/Nosremekk/Lectoria)"

# Controle de taxa (Rate limiting) e diagnóstico da última chamada
_LAST_OPENLIBRARY_CALL = 0.0
_OPENLIBRARY_MIN_INTERVAL = 0.8  # Intervalo defensivo entre chamadas à Open Library
_LAST_CROSSREF_CALL = 0.0
_CROSSREF_MIN_INTERVAL = 0.6
_GOOGLE_BOOKS_COOLDOWN_UNTIL = 0.0  # Timestamp de expiração do cooldown em caso de HTTP 429
_RECENT_CALL_ERRORS: List[Dict[str, str]] = []


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
# 2. EXTRAÇÃO LOCAL PROFUNDA E NORMALIZAÇÃO ROBUSTA (ETAPAS 1 E 2)
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


def normalize_author_name(raw_author: Optional[str]) -> Optional[str]:
    """
    Limpa nomes de autores inválidos/placeholders e converte o formato bibliográfico
    'Sobrenome, Nome' (ex: 'Stallings, William' -> 'William Stallings') quando aplicável.
    """
    if not raw_author:
        return None
    cleaned = str(raw_author).strip(" \t\r\n-–—:;,")
    if not cleaned:
        return None
    norm = normalize_text_for_matching(cleaned)
    if norm in (
        "autor desconhecido", "unknown", "autor nao informado", "sem autor",
        "admin", "user", "desconhecido", "none", "null", "na", "n a",
    ):
        return None
    # Se possui exatamente uma vírgula e parece inversão 'Sobrenome, Nome'
    if cleaned.count(",") == 1 and ";" not in cleaned and " e " not in cleaned.lower() and " and " not in cleaned.lower():
        left, right = [p.strip() for p in cleaned.split(",", 1)]
        if left and right and 1 <= len(left.split()) <= 4 and 1 <= len(right.split()) <= 4:
            if not any(ch.isdigit() for ch in cleaned):
                return f"{right} {left}".strip()
    return cleaned


def parse_structured_title_and_author(
    raw_title: Optional[str],
    raw_author: Optional[str] = None,
    parent_folder: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Etapa 2 — Normalização reversível e extração estruturada:
    Limpa ruídos de digitalização/download, separa volume, edição e subtítulo
    e identifica padrões 'Autor - Título' ou 'Título - Autor' sem descartar dados relevantes.
    """
    text = (raw_title or "").strip()
    # Remove extensões residuais no título
    text = re.sub(r"\.(?:pdf|epub|mobi|azw3|djvu|doc|docx|indd)$", "", text, flags=re.IGNORECASE).strip()

    # Remove tags de bibliotecas/scans e coleções entre parênteses/colchetes
    for pat in NOISE_BRACKET_PATTERNS:
        text = pat.sub("", text).strip()
    text = re.sub(r"[\(\[][^)\]]*(?:cole[cç][aã]o|acervo\s+digital)[^)\]]*[\)\]]", "", text, flags=re.IGNORECASE).strip()

    # Extrai Volume se presente
    volume = None
    m_vol = VOLUME_REGEX.search(text)
    if m_vol:
        vol_num = m_vol.group(1) or m_vol.group(2) or m_vol.group(3)
        volume = f"Vol. {vol_num}"
        text = (text[:m_vol.start()] + " " + text[m_vol.end():]).strip()

    # Extrai Edição se presente
    edition = None
    m_ed = EDITION_REGEX.search(text)
    if m_ed:
        edition = m_ed.group(1).strip()
        text = (text[:m_ed.start()] + " " + text[m_ed.end():]).strip()

    # Extrai ano entre parênteses no final, ex: "Sociedade do Cansaço (2015)"
    extracted_year = None
    m_end_year = re.search(r"[\(\[\-\s]+(19\d{2}|20[0-2]\d)[\)\]]?\s*$", text)
    if m_end_year:
        extracted_year = m_end_year.group(1)
        text = text[:m_end_year.start()].strip()

    # Substitui underscores múltiplos por espaço, preservando hífens separadores
    text = re.sub(r"_+", " ", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.")

    inferred_author = normalize_author_name(raw_author)

    # Se não há autor explícito e o título contém " - " ou " — ", avalia separação Autor / Título
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", text) if p.strip()]
    clean_title = text
    subtitle = None

    if len(parts) >= 2 and not inferred_author:
        first_p, last_p = parts[0], parts[-1]
        first_words = first_p.split()
        last_words = last_p.split()
        title_indicators = {
            "o", "a", "os", "as", "um", "uma", "do", "da", "dos", "das", "de", "em", "no", "na",
            "the", "an", "of", "in", "introducao", "manual", "curso", "topicos", "historia",
            "teoria", "critica", "sociedade", "sistemas", "arquitetura", "engenharia", "criptografia",
        }
        first_norm_tokens = set(normalize_text_for_matching(first_p).split())
        last_norm_tokens = set(normalize_text_for_matching(last_p).split())

        if "," in first_p and len(first_words) <= 5:
            inferred_author = first_p
            clean_title = " - ".join(parts[1:])
        elif "," in last_p and len(last_words) <= 5:
            inferred_author = last_p
            clean_title = " - ".join(parts[:-1])
        elif (
            1 <= len(first_words) <= 3
            and not (first_norm_tokens & title_indicators)
            and not any(c.isdigit() for c in first_p)
        ):
            inferred_author = first_p
            clean_title = " - ".join(parts[1:])
        elif (
            2 <= len(last_words) <= 4
            and not (last_norm_tokens & title_indicators)
            and not any(c.isdigit() for c in last_p)
        ):
            inferred_author = last_p
            clean_title = " - ".join(parts[:-1])

    # Verifica se a pasta pai fornece o nome do autor quando ainda ausente
    if not inferred_author and parent_folder:
        folder_clean = parent_folder.strip()
        if (
            folder_clean.lower() not in GENERIC_FOLDER_NAMES
            and 2 <= len(folder_clean.split()) <= 4
            and not any(c.isdigit() for c in folder_clean)
        ):
            inferred_author = folder_clean

    # Separa título principal e subtítulo (por ':' ou ' - ')
    main_title = clean_title
    if ":" in clean_title:
        t_parts = clean_title.split(":", 1)
        if len(t_parts[0].strip()) >= 3:
            main_title = t_parts[0].strip()
            subtitle = t_parts[1].strip()
    elif " - " in clean_title:
        t_parts = clean_title.split(" - ", 1)
        if len(t_parts[0].strip()) >= 3:
            main_title = t_parts[0].strip()
            subtitle = t_parts[1].strip()

    return {
        "title": clean_title or raw_title or "",
        "main_title": main_title or clean_title or raw_title or "",
        "subtitle": subtitle,
        "author": inferred_author,
        "edition": edition,
        "volume": volume,
        "publish_year": extracted_year,
    }


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


def _is_generic_or_noisy_title(title: Optional[str]) -> bool:
    """Detecta se um título interno de PDF é genérico ou gerado por processador de texto."""
    if not title or not title.strip():
        return True
    t = title.strip()
    if len(t) < 2:
        return True
    if GENERIC_TITLE_PATTERNS.match(t):
        return True
    lower = t.lower()
    if lower.endswith((".pdf", ".doc", ".docx", ".indd", ".pmd", ".qxd", ".ppt", ".pptx")):
        return True
    if lower.startswith(("microsoft word", "camscanner", "untitled", "sem titulo", "sem título")):
        return True
    return False


def extract_pdf_metadata_advanced(file_path: Path) -> Dict[str, Any]:
    """
    Extrai metadados profundos do PDF (Etapa 1):
    - Metadados do cabeçalho (/Title, /Author, /Subject, /Keywords, /CreationDate)
    - Varredura de texto das páginas iniciais (até 10) e finais (até 2, colofão) em busca de
      Ficha Catalográfica (CIP), ISBNs válidos, editora, edição e ano de publicação.
    - Combinação inteligente com nome do arquivo e pasta pai.
    """
    import pypdf

    title = None
    author = None
    publisher = None
    publish_year = None
    description = None
    isbn10 = None
    isbn13 = None
    edition = None
    volume = None
    subtitle = None
    subjects: List[str] = []
    page_count = 0

    try:
        with open(file_path, "rb") as fh:
            reader = pypdf.PdfReader(fh, strict=False)
            page_count = len(reader.pages)

            if reader.metadata:
                raw_title = reader.metadata.get("/Title")
                if raw_title and isinstance(raw_title, str) and not _is_generic_or_noisy_title(raw_title):
                    title = raw_title.strip()

                raw_author = reader.metadata.get("/Author")
                if raw_author and isinstance(raw_author, str) and raw_author.strip():
                    a_clean = raw_author.strip()
                    if normalize_text_for_matching(a_clean) not in ("unknown", "admin", "user", "usuario", "autor"):
                        author = a_clean

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

            # Varredura de páginas iniciais (até 10) e últimas 2 páginas (colofão) com limite de caracteres
            pages_to_scan = list(range(min(10, page_count)))
            for tail_idx in range(max(0, page_count - 2), page_count):
                if tail_idx not in pages_to_scan:
                    pages_to_scan.append(tail_idx)

            extracted_texts = []
            total_chars = 0
            for p_idx in pages_to_scan:
                if total_chars > 24000:
                    break
                try:
                    p_text = (reader.pages[p_idx].extract_text() or "")[:4000]
                    if p_text:
                        extracted_texts.append(p_text)
                        total_chars += len(p_text)
                except Exception:
                    pass

            combined_text = "\n".join(extracted_texts)
            if combined_text:
                # 1. Procura ISBNs válidos no texto
                found_isbns = extract_isbns_from_text(combined_text)
                if found_isbns:
                    first = found_isbns[0]
                    isbn13 = first["isbn13"]
                    isbn10 = first["isbn10"]

                # 2. Identifica Ficha Catalográfica (CIP) ou dados editoriais
                has_cip = any(marker in combined_text.lower() for marker in CIP_MARKERS)
                if has_cip or found_isbns:
                    years = YEAR_REGEX.findall(combined_text)
                    if years and not publish_year:
                        publish_year = int(years[-1])

                    for pub_pat in PUBLISHER_PATTERNS:
                        m_pub = pub_pat.search(combined_text)
                        if m_pub and not publisher:
                            publisher = m_pub.group(1).split("\n")[0].strip(" .,;-")
                            break

                m_ed = EDITION_REGEX.search(combined_text)
                if m_ed and not edition:
                    edition = m_ed.group(1).strip()

    except Exception as e:
        logger.debug("Falha na extração de metadados PDF para %s: %s", file_path.name, e)

    # Combina e limpa usando o parser estruturado de arquivo/pasta
    parent_name = file_path.parent.name if file_path.parent else None
    file_parsed = parse_structured_title_and_author(file_path.stem, raw_author=author, parent_folder=parent_name)

    if not title or _is_generic_or_noisy_title(title):
        title = file_parsed["title"] or file_path.stem.replace("_", " ").replace("-", " ").strip()
        subtitle = file_parsed.get("subtitle")
    else:
        title_parsed = parse_structured_title_and_author(title, raw_author=author, parent_folder=parent_name)
        title = title_parsed["title"] or title
        subtitle = title_parsed.get("subtitle") or file_parsed.get("subtitle")
        if not author and title_parsed.get("author"):
            author = title_parsed["author"]

    if not author and file_parsed.get("author"):
        author = file_parsed["author"]
    if not edition and file_parsed.get("edition"):
        edition = file_parsed["edition"]
    if not volume and file_parsed.get("volume"):
        volume = file_parsed["volume"]
    if not publish_year and file_parsed.get("publish_year"):
        try:
            publish_year = int(file_parsed["publish_year"])
        except ValueError:
            pass

    return {
        "title": title or file_path.name,
        "subtitle": subtitle,
        "author": author,
        "publisher": publisher,
        "publish_year": publish_year,
        "edition": edition,
        "volume": volume,
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
    - Verificação de proteção DRM e fallback estruturado de nome de arquivo/pasta.
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
    edition = None
    volume = None
    subtitle = None
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
                    clean = clean_isbn(text_val)
                    if len(clean) == 13 and validate_isbn13(clean) and not isbn13:
                        isbn13 = clean
                    elif len(clean) == 10 and validate_isbn10(clean) and not isbn10:
                        isbn10 = clean
                        if not isbn13:
                            isbn13 = isbn10_to_isbn13(clean)

    except Exception as e:
        logger.debug("Falha na extração de metadados EPUB para %s: %s", file_path.name, e)

    parent_name = file_path.parent.name if file_path.parent else None
    file_parsed = parse_structured_title_and_author(file_path.stem, raw_author=author, parent_folder=parent_name)

    if not title or _is_generic_or_noisy_title(title):
        title = file_parsed["title"] or file_path.stem.replace("_", " ").replace("-", " ").strip()
        subtitle = file_parsed.get("subtitle")
    if not author and file_parsed.get("author"):
        author = file_parsed["author"]
    if not edition and file_parsed.get("edition"):
        edition = file_parsed["edition"]
    if not volume and file_parsed.get("volume"):
        volume = file_parsed["volume"]

    return {
        "title": title or file_path.name,
        "subtitle": subtitle,
        "author": author,
        "publisher": publisher,
        "publish_year": publish_year,
        "edition": edition,
        "volume": volume,
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
# 3. CACHE LOCAL, MÉTRICAS POR PROVEDOR E REQUISIÇÕES HTTP RESILIENTES
# ==============================================================================

def record_provider_stat(
    provider: str,
    outcome: str,
    error_message: Optional[str] = None,
    cooldown_until_ts: Optional[float] = None,
) -> None:
    """
    Atualiza as métricas persistentes do provedor bibliográfico (openlibrary, googlebooks, crossref).
    outcome: 'success', 'not_found', 'rate_limited', 'error', 'parse_error'
    """
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cooldown_iso = (
            datetime.fromtimestamp(cooldown_until_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if cooldown_until_ts
            else None
        )
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO metadata_provider_stats (provider, last_status) VALUES (?, 'healthy');",
                (provider,),
            )
            if outcome == "success":
                cursor.execute(
                    """
                    UPDATE metadata_provider_stats
                    SET total_requests = total_requests + 1,
                        success_count = success_count + 1,
                        last_status = 'healthy',
                        last_call_at = ?
                    WHERE provider = ?;
                    """,
                    (now_iso, provider),
                )
            elif outcome == "not_found":
                cursor.execute(
                    """
                    UPDATE metadata_provider_stats
                    SET total_requests = total_requests + 1,
                        not_found_count = not_found_count + 1,
                        last_status = 'healthy',
                        last_call_at = ?
                    WHERE provider = ?;
                    """,
                    (now_iso, provider),
                )
            elif outcome == "rate_limited":
                cursor.execute(
                    """
                    UPDATE metadata_provider_stats
                    SET total_requests = total_requests + 1,
                        rate_limit_count = rate_limit_count + 1,
                        last_status = 'rate_limited',
                        last_error = ?,
                        cooldown_until = COALESCE(?, cooldown_until),
                        last_call_at = ?
                    WHERE provider = ?;
                    """,
                    (error_message or "HTTP 429 Too Many Requests", cooldown_iso, now_iso, provider),
                )
            elif outcome == "parse_error":
                cursor.execute(
                    """
                    UPDATE metadata_provider_stats
                    SET total_requests = total_requests + 1,
                        parse_error_count = parse_error_count + 1,
                        error_count = error_count + 1,
                        last_status = 'degraded',
                        last_error = ?,
                        last_call_at = ?
                    WHERE provider = ?;
                    """,
                    (error_message or "Erro de parsing JSON", now_iso, provider),
                )
            else:
                cursor.execute(
                    """
                    UPDATE metadata_provider_stats
                    SET total_requests = total_requests + 1,
                        error_count = error_count + 1,
                        last_status = 'error',
                        last_error = ?,
                        last_call_at = ?
                    WHERE provider = ?;
                    """,
                    (error_message or "Falha de comunicação HTTP", now_iso, provider),
                )
    except Exception as e:
        logger.debug("Não foi possível atualizar estatísticas do provedor %s: %s", provider, e)


def get_providers_diagnostics() -> List[Dict[str, Any]]:
    """Retorna o estado e as métricas acumuladas de cada provedor bibliográfico."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM metadata_provider_stats ORDER BY provider ASC;")
            rows = [dict(r) for r in cursor.fetchall()]
            if rows:
                return rows
    except Exception:
        pass
    return [
        {"provider": "openlibrary", "total_requests": 0, "success_count": 0, "error_count": 0, "rate_limit_count": 0, "last_status": "healthy"},
        {"provider": "googlebooks", "total_requests": 0, "success_count": 0, "error_count": 0, "rate_limit_count": 0, "last_status": "healthy"},
        {"provider": "crossref", "total_requests": 0, "success_count": 0, "error_count": 0, "rate_limit_count": 0, "last_status": "healthy"},
    ]


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
    - Respeita limites de taxa (rate limiting) e backoff em caso de 429.
    - Registra diagnósticos estruturados por provedor (distinguindo 429, timeout, erro de rede e erro de parsing).
    """
    global _GOOGLE_BOOKS_COOLDOWN_UNTIL
    if "googleapis.com" in url and time.time() < _GOOGLE_BOOKS_COOLDOWN_UNTIL:
        logger.debug("Google Books em cooldown de taxa (HTTP 429) até %s. Ignorando requisição.", _GOOGLE_BOOKS_COOLDOWN_UNTIL)
        _RECENT_CALL_ERRORS.append({"source": source, "type": "rate_limited", "message": "Google Books em cooldown por HTTP 429"})
        return None

    if cache_key:
        cached = get_cached_api_response(cache_key)
        if cached is not None:
            return cached

    # Rate limiting defensivo para a Open Library e Crossref
    global _LAST_OPENLIBRARY_CALL, _LAST_CROSSREF_CALL
    if "openlibrary.org" in url:
        elapsed = time.time() - _LAST_OPENLIBRARY_CALL
        if elapsed < _OPENLIBRARY_MIN_INTERVAL:
            time.sleep(_OPENLIBRARY_MIN_INTERVAL - elapsed)
        _LAST_OPENLIBRARY_CALL = time.time()
    elif "crossref.org" in url:
        elapsed = time.time() - _LAST_CROSSREF_CALL
        if elapsed < _CROSSREF_MIN_INTERVAL:
            time.sleep(_CROSSREF_MIN_INTERVAL - elapsed)
        _LAST_CROSSREF_CALL = time.time()

    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if status == 200:
                raw_body = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw_body)
                if cache_key:
                    set_cached_api_response(cache_key, source, parsed, status_code=status)
                record_provider_stat(source, "success")
                return parsed
    except urllib.error.HTTPError as e:
        logger.debug("HTTPError %s na consulta para %s: %s", e.code, url, e)
        if e.code == 429:
            if "googleapis.com" in url:
                _GOOGLE_BOOKS_COOLDOWN_UNTIL = time.time() + 60.0
            record_provider_stat(source, "rate_limited", f"HTTP 429 Too Many Requests ({source})", _GOOGLE_BOOKS_COOLDOWN_UNTIL or None)
            _RECENT_CALL_ERRORS.append({"source": source, "type": "rate_limited", "message": f"HTTP 429 em {source}"})
        elif e.code == 404:
            if cache_key:
                set_cached_api_response(cache_key, source, {}, status_code=404, ttl_days=3)
            record_provider_stat(source, "not_found")
        else:
            record_provider_stat(source, "error", f"HTTP {e.code} em {source}")
            _RECENT_CALL_ERRORS.append({"source": source, "type": "network_error", "message": f"HTTP {e.code} em {source}"})
    except json.JSONDecodeError as e:
        logger.debug("Falha de parsing JSON de %s: %s", url, e)
        record_provider_stat(source, "parse_error", f"JSON inválido de {source}")
        _RECENT_CALL_ERRORS.append({"source": source, "type": "parse_error", "message": f"Resposta malformada de {source}"})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug("Falha de rede/timeout para %s: %s", url, e)
        err_kind = "timeout" if isinstance(e, TimeoutError) or "timed out" in str(e).lower() else "network_error"
        record_provider_stat(source, "error", f"Falha de rede ({err_kind}): {type(e).__name__}")
        _RECENT_CALL_ERRORS.append({"source": source, "type": err_kind, "message": f"Falha de conexão com {source}"})

    return None


# ==============================================================================
# 4. ADAPTADORES DE PROVEDORES BIBLIOGRÁFICOS (ETAPA 3)
#    Open Library, Google Books e Crossref
# ==============================================================================

class OpenLibraryClient:
    """Adaptador para a API pública da Open Library (Internet Archive)."""

    @staticmethod
    def lookup_isbn(isbn: str) -> Optional[Dict[str, Any]]:
        """Consulta obra e edição por ISBN na Open Library."""
        clean = clean_isbn(isbn)
        if not (validate_isbn13(clean) or validate_isbn10(clean)):
            return None

        item_cache_key = f"ol:item:{clean}"
        cached_result = get_cached_api_response(item_cache_key)
        if cached_result is not None:
            if cached_result.get("not_found"):
                return None
            return cached_result

        cache_key = f"ol:isbn:{clean}"
        url = f"{OPENLIBRARY_BASE_URL}/isbn/{clean}.json"
        data = _safe_http_get_json(url, cache_key=cache_key, source="openlibrary")
        if not data:
            bib_url = f"{OPENLIBRARY_BASE_URL}/api/books?bibkeys=ISBN:{clean}&format=json&jscmd=data"
            bib_data = _safe_http_get_json(bib_url, cache_key=f"ol:bib:{clean}", source="openlibrary")
            if bib_data and f"ISBN:{clean}" in bib_data:
                item = bib_data[f"ISBN:{clean}"]
                parsed = OpenLibraryClient._parse_bib_item(item, clean)
                set_cached_api_response(item_cache_key, "openlibrary", parsed, status_code=200)
                return parsed
            return None

        parsed = OpenLibraryClient._parse_edition_json(data, clean)
        set_cached_api_response(item_cache_key, "openlibrary", parsed, status_code=200)
        return parsed

    @staticmethod
    def search(title: str, author: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Busca livros por título e autor na Open Library, com fallback para busca livre q=."""
        query_params = {"title": title, "limit": limit}
        if author:
            query_params["author"] = author

        query_str = urllib.parse.urlencode(query_params)
        cache_key = f"ol:search:{query_str}"
        url = f"{OPENLIBRARY_BASE_URL}/search.json?{query_str}"

        data = _safe_http_get_json(url, cache_key=cache_key, source="openlibrary")
        docs = data.get("docs", []) if data and isinstance(data, dict) else []

        # Fallback: se a busca estrita por campo não encontrou documentos, tenta busca geral q= com título principal limpo
        if not docs:
            norm_info = parse_structured_title_and_author(title, raw_author=author)
            main_t = norm_info.get("main_title") or title
            clean_a = norm_info.get("author") or author
            q_free = f"{main_t} {clean_a}".strip() if clean_a else main_t.strip()
            if q_free and (q_free != title or author):
                q_params = urllib.parse.urlencode({"q": q_free, "limit": limit})
                fb_url = f"{OPENLIBRARY_BASE_URL}/search.json?{q_params}"
                fb_data = _safe_http_get_json(fb_url, cache_key=f"ol:q:{q_params}", source="openlibrary")
                if fb_data and isinstance(fb_data, dict):
                    docs = fb_data.get("docs", [])

        results = []
        for doc in docs[:limit]:
            parsed = OpenLibraryClient._parse_search_doc(doc)
            if parsed:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_edition_json(data: Dict[str, Any], searched_isbn: str) -> Dict[str, Any]:
        """Normaliza o JSON retornado por /isbn/{isbn}.json."""
        title = data.get("title", "")
        subtitle = data.get("subtitle")
        if subtitle and subtitle.lower() not in title.lower():
            full_title = f"{title}: {subtitle}"
        else:
            full_title = title

        authors = []
        for a in data.get("authors", []):
            if isinstance(a, dict) and "name" in a and a["name"]:
                authors.append(a["name"])
            elif isinstance(a, str):
                authors.append(a)
        author = ", ".join(authors) if authors else None

        publish_year = None
        date_str = data.get("publish_date", "")
        m_year = YEAR_REGEX.search(str(date_str))
        if m_year:
            publish_year = int(m_year.group(1))

        publishers = data.get("publishers", [])
        publisher = publishers[0] if publishers else None

        isbn10_list = data.get("isbn_10", [])
        isbn13_list = data.get("isbn_13", [])
        isbn10 = isbn10_list[0] if isbn10_list else (searched_isbn if len(searched_isbn) == 10 else None)
        isbn13 = isbn13_list[0] if isbn13_list else (searched_isbn if len(searched_isbn) == 13 else None)

        cover_url = None
        covers = data.get("covers", [])
        if covers and isinstance(covers[0], int) and covers[0] > 0:
            cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/id/{covers[0]}-L.jpg"
        elif isbn13:
            cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/isbn/{isbn13}-L.jpg"

        works = data.get("works", [])
        work_id = works[0].get("key", "").split("/")[-1] if works and isinstance(works[0], dict) else None

        # Tenta recuperar sinopse (description) e subjects
        description = None
        raw_desc = data.get("description")
        if isinstance(raw_desc, dict) and "value" in raw_desc:
            description = raw_desc["value"]
        elif isinstance(raw_desc, str):
            description = raw_desc

        subjects = list(data.get("subjects", []))
        if not subjects and "genres" in data:
            subjects = list(data.get("genres", []))

        # Se faltar descrição ou subjects e houver work_id, consulta o Work na Open Library
        if work_id and (not description or not subjects):
            work_url = f"{OPENLIBRARY_BASE_URL}/works/{work_id}.json"
            work_data = _safe_http_get_json(work_url, cache_key=f"ol:work:{work_id}", source="openlibrary")
            if work_data and isinstance(work_data, dict):
                if not description:
                    w_desc = work_data.get("description")
                    if isinstance(w_desc, dict) and "value" in w_desc:
                        description = w_desc["value"]
                    elif isinstance(w_desc, str):
                        description = w_desc
                if not subjects and "subjects" in work_data:
                    subjects = [s if isinstance(s, str) else s.get("name", "") for s in work_data.get("subjects", [])]

        return {
            "source": "openlibrary",
            "external_id": data.get("key", "").split("/")[-1] or searched_isbn,
            "work_id": work_id,
            "title": full_title or title,
            "author": author,
            "publisher": publisher,
            "publish_year": publish_year,
            "edition": data.get("edition_name"),
            "description": description,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": [s for s in subjects if s],
            "raw_data": data,
        }

    @staticmethod
    def _parse_bib_item(item: Dict[str, Any], searched_isbn: str) -> Dict[str, Any]:
        """Normaliza o JSON retornado por /api/books?jscmd=data."""
        title = item.get("title", "")
        authors = [a.get("name") for a in item.get("authors", []) if isinstance(a, dict) and "name" in a]
        author = ", ".join(authors) if authors else None

        publish_year = None
        m_year = YEAR_REGEX.search(str(item.get("publish_date", "")))
        if m_year:
            publish_year = int(m_year.group(1))

        publishers = [p.get("name") for p in item.get("publishers", []) if isinstance(p, dict) and "name" in p]
        publisher = publishers[0] if publishers else None

        subjects = [s.get("name") for s in item.get("subjects", []) if isinstance(s, dict) and "name" in s]

        cover_url = None
        if "cover" in item and isinstance(item["cover"], dict):
            cover_url = item["cover"].get("large") or item["cover"].get("medium")

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
            if len(clean) == 13 and validate_isbn13(clean) and not isbn13:
                isbn13 = clean
            elif len(clean) == 10 and validate_isbn10(clean) and not isbn10:
                isbn10 = clean

        cover_id = doc.get("cover_i")
        cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/id/{cover_id}-L.jpg" if cover_id else None

        work_key = doc.get("key", "").split("/")[-1]
        subjects = doc.get("subject", [])[:10]
        langs = doc.get("language", [])
        language = langs[0] if langs else None

        return {
            "source": "openlibrary",
            "external_id": work_key or (isbn13 or isbn10 or title),
            "work_id": work_key,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_year": first_publish_year,
            "language": language,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": subjects,
            "raw_data": doc,
        }


class GoogleBooksClient:
    """Adaptador para a Google Books API (fonte complementar com forte cobertura em língua portuguesa)."""

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
        """Busca livros por título e autor na Google Books API, com fallback de consulta aberta."""
        query_parts = [f"intitle:{title}"]
        if author:
            query_parts.append(f"inauthor:{author}")

        query_str = "+".join(query_parts)
        encoded_query = urllib.parse.quote(query_str, safe="+:")
        cache_key = f"gb:search:{encoded_query}:{limit}"
        url = f"{GOOGLE_BOOKS_BASE_URL}/volumes?q={encoded_query}&maxResults={limit}"

        data = _safe_http_get_json(url, cache_key=cache_key, source="googlebooks")
        items = data.get("items", []) if data and isinstance(data, dict) else []

        # Fallback: se intitle:/inauthor: estrito não retornou resultados, consulta texto livre limpo
        if not items:
            norm_info = parse_structured_title_and_author(title, raw_author=author)
            main_t = norm_info.get("main_title") or title
            clean_a = norm_info.get("author") or author
            free_q = f"{main_t} {clean_a}".strip() if clean_a else main_t.strip()
            if free_q:
                enc_free = urllib.parse.quote(free_q)
                fb_url = f"{GOOGLE_BOOKS_BASE_URL}/volumes?q={enc_free}&maxResults={limit}"
                fb_data = _safe_http_get_json(fb_url, cache_key=f"gb:free:{enc_free}:{limit}", source="googlebooks")
                if fb_data and isinstance(fb_data, dict):
                    items = fb_data.get("items", [])

        results = []
        for item in items:
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
        subtitle = info.get("subtitle")

        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else None

        publish_year = None
        m_year = YEAR_REGEX.search(str(info.get("publishedDate", "")))
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
            "subtitle": subtitle,
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


class CrossrefClient:
    """Adaptador independente para a API pública Crossref (obras acadêmicas, técnicas e científicas)."""

    @staticmethod
    def lookup_isbn(isbn: str) -> Optional[Dict[str, Any]]:
        """Busca livro/monografia por ISBN na Crossref."""
        clean = clean_isbn(isbn)
        if not (validate_isbn13(clean) or validate_isbn10(clean)):
            return None
        cache_key = f"cr:isbn:{clean}"
        url = f"{CROSSREF_BASE_URL}/works?filter=isbn:{clean}&rows=2"
        data = _safe_http_get_json(url, cache_key=cache_key, source="crossref")
        items = data.get("message", {}).get("items", []) if data and isinstance(data, dict) else []
        if not items:
            return None
        return CrossrefClient._parse_work_item(items[0])

    @staticmethod
    def search(title: str, author: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        """Busca livros técnicos e acadêmicos por título e autor na Crossref."""
        norm_info = parse_structured_title_and_author(title, raw_author=author)
        main_t = norm_info.get("main_title") or title
        clean_a = norm_info.get("author") or author
        params: Dict[str, Any] = {
            "query.title": main_t,
            "filter": "type:book,type:monograph,type:edited-book,type:reference-book",
            "rows": limit,
        }
        if clean_a:
            params["query.author"] = clean_a
        query_str = urllib.parse.urlencode(params)
        cache_key = f"cr:search:{query_str}"
        url = f"{CROSSREF_BASE_URL}/works?{query_str}"

        data = _safe_http_get_json(url, cache_key=cache_key, source="crossref")
        items = data.get("message", {}).get("items", []) if data and isinstance(data, dict) else []
        results = []
        for item in items[:limit]:
            parsed = CrossrefClient._parse_work_item(item)
            if parsed:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_work_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        titles = item.get("title", [])
        if not titles:
            return None
        title = titles[0]
        subtitles = item.get("subtitle", [])
        subtitle = subtitles[0] if subtitles else None

        authors = []
        for a in item.get("author", []):
            if isinstance(a, dict):
                given = (a.get("given") or "").strip()
                family = (a.get("family") or "").strip()
                full = f"{given} {family}".strip() or a.get("name", "")
                if full:
                    authors.append(full)
        author = ", ".join(authors) if authors else None

        publish_year = None
        for date_field in ("published-print", "published-online", "issued", "created"):
            date_parts = item.get(date_field, {}).get("date-parts", [])
            if date_parts and date_parts[0] and isinstance(date_parts[0][0], int):
                publish_year = date_parts[0][0]
                break

        isbn10 = None
        isbn13 = None
        for raw_i in item.get("ISBN", []):
            c = clean_isbn(raw_i)
            if len(c) == 13 and validate_isbn13(c) and not isbn13:
                isbn13 = c
            elif len(c) == 10 and validate_isbn10(c) and not isbn10:
                isbn10 = c

        cover_url = f"{OPENLIBRARY_COVERS_BASE}/b/isbn/{isbn13}-L.jpg" if isbn13 else None

        return {
            "source": "crossref",
            "external_id": item.get("DOI") or isbn13 or title,
            "work_id": item.get("DOI"),
            "title": title,
            "subtitle": subtitle,
            "author": author,
            "publisher": item.get("publisher"),
            "publish_year": publish_year,
            "language": item.get("language"),
            "description": None,
            "edition": item.get("edition-number"),
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": item.get("subject", [])[:8],
            "raw_data": item,
        }


class BrasilApiClient:
    """Adaptador para a BrasilAPI (consulta direta ao acervo brasileiro CBL/Mercado Editorial por ISBN)."""

    @staticmethod
    def lookup_isbn(isbn: str) -> Optional[Dict[str, Any]]:
        clean = clean_isbn(isbn)
        if not (validate_isbn13(clean) or validate_isbn10(clean)):
            return None

        cache_key = f"brasilapi:isbn:{clean}"
        url = f"{BRASILAPI_BASE_URL}/{clean}"
        data = _safe_http_get_json(url, cache_key=cache_key, source="brasilapi", timeout=6.0)
        if not data or not isinstance(data, dict) or not data.get("title"):
            return None

        title = data.get("title")
        subtitle = data.get("subtitle")
        full_title = f"{title}: {subtitle}" if (subtitle and subtitle.lower() not in title.lower()) else title

        authors_list = data.get("authors") or []
        authors_str = ", ".join(authors_list) if isinstance(authors_list, list) else str(authors_list)
        norm_author = normalize_author_name(authors_str)

        subjects = data.get("subjects") or []
        if isinstance(subjects, str):
            subjects = [subjects]

        cover_url = data.get("cover_url")
        year = None
        if data.get("year"):
            try:
                year = int(data["year"])
            except Exception:
                pass

        isbn_val = clean_isbn(data.get("isbn") or clean)
        isbn10 = isbn_val if len(isbn_val) == 10 else None
        isbn13 = isbn_val if len(isbn_val) == 13 else isbn10_to_isbn13(isbn_val)

        return {
            "source": "brasilapi",
            "external_id": clean,
            "work_id": None,
            "title": full_title,
            "subtitle": subtitle,
            "author": norm_author,
            "publisher": data.get("publisher"),
            "publish_year": year,
            "language": "pt",
            "description": data.get("synopsis"),
            "edition": data.get("edition"),
            "isbn10": isbn10,
            "isbn13": isbn13,
            "cover_url": cover_url,
            "subjects": [s for s in subjects if s],
            "raw_data": data,
        }



# ==============================================================================
# 5. CORRESPONDÊNCIA INTELIGENTE E DECISÃO EXPLICÁVEL (ETAPAS 4 E 5)
# ==============================================================================

def _calculate_author_similarity(author1: Optional[str], author2: Optional[str]) -> float:
    """
    Calcula similaridade entre nomes de autores tratando inversões 'Sobrenome, Nome',
    múltiplos coautores e abreviações de nomes do meio.
    """
    if not author1 or not author2:
        return 0.0
    sim_direct = calculate_string_similarity(author1, author2)
    if sim_direct >= 0.85:
        return sim_direct

    tokens1 = [t for t in normalize_text_for_matching(author1).split() if len(t) > 1]
    tokens2 = [t for t in normalize_text_for_matching(author2).split() if len(t) > 1]
    if not tokens1 or not tokens2:
        return sim_direct

    set1, set2 = set(tokens1), set(tokens2)
    common = set1.intersection(set2)
    if not common:
        return 0.0

    # Verifica se o último sobrenome coincide
    surname_match = (tokens1[-1] in set2) or (tokens2[-1] in set1) or (tokens1[0] in set2)
    overlap_ratio = len(common) / min(len(set1), len(set2))
    score = (overlap_ratio * 0.75) + (0.25 if surname_match else 0.0)
    return max(sim_direct, min(1.0, score))


def score_candidate_detailed(book_meta: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[float, str, Dict[str, Any]]:
    """
    Atribui uma pontuação ponderada e explicável (0.0 a 100.0) a um candidato bibliográfico:
    - ISBN exato (ISBN-13 ou ISBN-10): +55 pontos
    - Similaridade de título (considerando título completo e título principal sem ruído): até +35 pontos
    - Correspondência de autor (tratando 'Sobrenome, Nome' e coautores): até +25 pontos
    - Coerência de ano, editora, idioma, edição/volume: até +12 pontos
    - Tratamento inteligente quando o arquivo local não possui autor nem ISBN embutido.
    """
    breakdown: Dict[str, float] = {}
    score = 0.0

    # Normaliza título/autor do livro local (caso venha direto do nome do arquivo)
    local_parsed = parse_structured_title_and_author(
        book_meta.get("title"),
        raw_author=book_meta.get("author"),
    )
    local_title = book_meta.get("title") or local_parsed["title"]
    local_main_title = local_parsed.get("main_title") or local_title
    local_author = book_meta.get("author") or local_parsed.get("author")

    cand_title = candidate.get("title") or ""
    cand_parsed = parse_structured_title_and_author(cand_title, raw_author=candidate.get("author"))
    cand_main_title = cand_parsed.get("main_title") or cand_title

    # 1. Checagem de ISBN
    book_isbn13 = clean_isbn(book_meta.get("isbn13"))
    book_isbn10 = clean_isbn(book_meta.get("isbn10"))
    cand_isbn13 = clean_isbn(candidate.get("isbn13"))
    cand_isbn10 = clean_isbn(candidate.get("isbn10"))

    has_local_isbn = bool(book_isbn13 or book_isbn10)
    isbn_matched = False
    if book_isbn13 and cand_isbn13 and book_isbn13 == cand_isbn13:
        score += 55.0
        breakdown["isbn"] = 55.0
        isbn_matched = True
    elif book_isbn10 and cand_isbn10 and book_isbn10 == cand_isbn10:
        score += 55.0
        breakdown["isbn"] = 55.0
        isbn_matched = True
    elif book_isbn13 and cand_isbn10 and isbn10_to_isbn13(cand_isbn10) == book_isbn13:
        score += 55.0
        breakdown["isbn"] = 55.0
        isbn_matched = True

    # 2. Similaridade de Título (compara título completo e título principal sem subtítulo/volume)
    sim_full = calculate_string_similarity(local_title, cand_title)
    sim_main = calculate_string_similarity(local_main_title, cand_main_title)
    title_sim = max(sim_full, sim_main * 0.96)
    title_pts = round(title_sim * 30.0, 2)
    score += title_pts
    breakdown["title"] = title_pts

    # 3. Similaridade de Autor
    author_sim = 0.0
    if local_author and candidate.get("author"):
        author_sim = _calculate_author_similarity(local_author, candidate.get("author"))
        author_pts = round(author_sim * 20.0, 2)
        score += author_pts
        breakdown["author"] = author_pts
    elif isbn_matched:
        score += 10.0
        breakdown["author_isbn_bonus"] = 10.0

    # Bônus para forte correlação de Título + Autor quando não há ISBN batido
    if not isbn_matched and title_sim >= 0.5 and author_sim >= 0.5:
        bonus = round(((title_sim + author_sim) / 2.0) * 32.0, 2)
        # Se o livro local possuía um ISBN explícito mas o candidato não tem esse ISBN,
        # limita o bônus se o título não for idêntico (ex: outra obra/antologia do mesmo autor)
        if has_local_isbn and sim_full < 0.85:
            bonus = min(bonus, 22.0)
        score += bonus
        breakdown["title_author_synergy"] = bonus
    elif not isbn_matched and not local_author and not has_local_isbn and title_sim >= 0.80:
        # Quando o PDF local não tem autor nem ISBN, um título fortemente compatível deve alcançar 'needs_review' (>= 55)
        no_author_bonus = round(title_sim * 32.0, 2)
        score += no_author_bonus
        breakdown["title_only_fallback"] = no_author_bonus

    # 4. Ano de Publicação
    book_year = book_meta.get("publish_year")
    cand_year = candidate.get("publish_year")
    if book_year and cand_year:
        try:
            diff = abs(int(book_year) - int(cand_year))
            if diff <= 1:
                score += 4.0
                breakdown["year"] = 4.0
            elif diff <= 5:
                score += 2.0
                breakdown["year"] = 2.0
        except (ValueError, TypeError):
            pass

    # 5. Editora
    book_pub = book_meta.get("publisher")
    cand_pub = candidate.get("publisher")
    if book_pub and cand_pub:
        pub_sim = calculate_string_similarity(book_pub, cand_pub)
        if pub_sim > 0.3:
            pub_pts = round(pub_sim * 4.0, 2)
            score += pub_pts
            breakdown["publisher"] = pub_pts

    # 6. Idioma e Edição/Volume
    book_lang = (book_meta.get("language") or "").lower()[:2]
    cand_lang = (candidate.get("language") or "").lower()[:2]
    if book_lang and cand_lang and book_lang == cand_lang:
        score += 3.0
        breakdown["language"] = 3.0

    local_ed = book_meta.get("edition") or local_parsed.get("edition")
    cand_ed = candidate.get("edition") or cand_parsed.get("edition")
    if local_ed and cand_ed and calculate_string_similarity(str(local_ed), str(cand_ed)) >= 0.6:
        score += 3.0
        breakdown["edition"] = 3.0

    # Bônus de coerência multi-fonte (preenchido por identify_book_metadata quando >=2 fontes concordam)
    if candidate.get("multi_source_confirmed"):
        score += 8.0
        breakdown["multi_source_coherence"] = 8.0

    final_score = round(min(100.0, score), 1)

    if final_score >= 85.0:
        confidence = "reliable"
    elif final_score >= 50.0:
        confidence = "needs_review"
    else:
        confidence = "insufficient"

    return final_score, confidence, breakdown


def score_candidate(book_meta: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[float, str]:
    """Compatibilidade direta retornando (score, confidence_level)."""
    final_score, confidence, _ = score_candidate_detailed(book_meta, candidate)
    return final_score, confidence


def normalize_for_comparison(text: Optional[str]) -> str:
    """Alias para normalização textual comparativa."""
    return normalize_text_for_matching(text)


def record_metadata_provenance(
    cursor,
    book_id: str,
    fields: Dict[str, Any],
    source: str,
    confidence: float = 100.0,
) -> None:
    """Registra a origem (proveniência) de cada campo de metadados atualizado."""
    try:
        for fname, fval in fields.items():
            if fval is None or fname in ("metadata_updated_at", "updated_at", "manual_lock_fields"):
                continue
            cursor.execute(
                """
                INSERT INTO metadata_provenance (book_id, field_name, field_value, source, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(book_id, field_name) DO UPDATE SET
                    field_value = excluded.field_value,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (book_id, fname, str(fval)[:1000], source, float(confidence)),
            )
    except Exception as e:
        logger.debug("Falha ao registrar proveniência para %s: %s", book_id, e)


def identify_book_metadata_with_diagnostics(book_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executa o pipeline completo de 5 etapas e retorna dicionário estruturado:
    {
        'candidates': [...],
        'decision': 'identified' | 'needs_review' | 'ambiguous' | 'not_found' | 'rate_limited' | 'technical_error',
        'status': (mesmo valor de decision),
        'error_reason': Optional[str],
        'top_score': float,
        'errors': [...]
    }
    """
    _RECENT_CALL_ERRORS.clear()
    candidates = identify_book_metadata(book_meta)
    errors = list(_RECENT_CALL_ERRORS)
    err_msg = "; ".join(f"{e.get('provider')}: {e.get('detail')}" for e in errors) if errors else None

    if not candidates:
        if any(e.get("type") == "rate_limited" for e in errors):
            return {
                "candidates": [],
                "decision": "rate_limited",
                "status": "rate_limited",
                "error_reason": err_msg or "Rate limit atingido nos provedores externos",
                "top_score": 0.0,
                "errors": errors,
            }
        if errors:
            return {
                "candidates": [],
                "decision": "technical_error",
                "status": "technical_error",
                "error_reason": err_msg or "Erro técnico ao consultar provedores externos",
                "top_score": 0.0,
                "errors": errors,
            }
        return {
            "candidates": [],
            "decision": "not_found",
            "status": "not_found",
            "error_reason": None,
            "top_score": 0.0,
            "errors": [],
        }

    best = candidates[0]
    top_score = best.get("score", 0.0)
    top_conf = best.get("confidence_level", "insufficient")

    # Detecta ambiguidade: se há 2 candidatos de obras/autores distintos com pontuações próximas (>= 55)
    if len(candidates) >= 2 and top_score >= 55.0 and not clean_isbn(book_meta.get("isbn13")):
        second = candidates[1]
        sec_score = second.get("score", 0.0)
        if abs(top_score - sec_score) <= 4.0:
            same_author = _calculate_author_similarity(best.get("author"), second.get("author")) >= 0.7
            same_title = calculate_string_similarity(best.get("title"), second.get("title")) >= 0.75
            if not (same_author and same_title):
                best["confidence_level"] = "ambiguous"
                return {
                    "candidates": candidates,
                    "decision": "ambiguous",
                    "status": "ambiguous",
                    "error_reason": "Múltiplos candidatos com pontuação próxima; requer revisão humana",
                    "top_score": top_score,
                    "errors": errors,
                }

    if top_conf == "reliable":
        decision = "identified"
    elif top_conf in ("needs_review", "ambiguous"):
        decision = top_conf
    else:
        if any(e.get("type") == "rate_limited" for e in errors):
            decision = "rate_limited"
        elif errors:
            decision = "technical_error"
        else:
            decision = "not_found"

    return {
        "candidates": candidates,
        "decision": decision,
        "status": decision,
        "error_reason": err_msg if decision in ("rate_limited", "technical_error") else None,
        "top_score": top_score,
        "errors": errors,
    }


def identify_book_metadata(book_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Executa estratégia completa de busca e identificação bibliográfica (Etapas 3, 4 e 5):
    1. Consulta por ISBN na Open Library, Google Books e Crossref.
    2. Normaliza título/autor (extraindo autor de nomes de arquivo 'Autor - Título' quando necessário).
    3. Busca textual na Open Library, Google Books e Crossref.
    4. Verifica coerência multi-fonte e pontua todos os candidatos com explicação detalhada.
    Retorna lista ordenada de candidatos por pontuação decrescente.
    """
    raw_candidates: List[Dict[str, Any]] = []
    seen_ids = set()

    isbn13 = book_meta.get("isbn13")
    isbn10 = book_meta.get("isbn10")
    primary_isbn = isbn13 or isbn10

    # 1. Consulta por ISBN (Open Library, Google Books e BrasilAPI)
    if primary_isbn:
        # BrasilAPI prioritária para edições brasileiras (ISBN 97885 / 97865)
        br_res = BrasilApiClient.lookup_isbn(primary_isbn)
        if br_res:
            raw_candidates.append(br_res)
            seen_ids.add(f"brasilapi:{br_res['external_id']}")

        ol_res = OpenLibraryClient.lookup_isbn(primary_isbn)
        if ol_res:
            raw_candidates.append(ol_res)
            seen_ids.add(f"ol:{ol_res['external_id']}")

        gb_res = GoogleBooksClient.lookup_isbn(primary_isbn)
        if gb_res and f"gb:{gb_res['external_id']}" not in seen_ids:
            raw_candidates.append(gb_res)
            seen_ids.add(f"gb:{gb_res['external_id']}")

        cr_res = CrossrefClient.lookup_isbn(primary_isbn)
        if cr_res and f"cr:{cr_res['external_id']}" not in seen_ids:
            raw_candidates.append(cr_res)
            seen_ids.add(f"cr:{cr_res['external_id']}")

    # Avalia se já há match confiável por ISBN (>= 85)
    if raw_candidates:
        for c in raw_candidates:
            s, conf, bd = score_candidate_detailed(book_meta, c)
            c["score"] = s
            c["confidence_level"] = conf
            c["score_breakdown"] = bd
        raw_candidates.sort(key=lambda c: c["score"], reverse=True)
        if raw_candidates[0]["score"] >= 85.0:
            return raw_candidates

    # 2. Normalização para busca por Título e Autor
    parsed_local = parse_structured_title_and_author(
        book_meta.get("title"),
        raw_author=book_meta.get("author"),
    )
    search_title = parsed_local.get("main_title") or book_meta.get("title")
    search_author = book_meta.get("author") or parsed_local.get("author")

    if search_title:
        # Open Library Search
        ol_results = OpenLibraryClient.search(search_title, author=search_author, limit=4)
        for cand in ol_results:
            cid = f"ol:{cand.get('external_id')}"
            if cid not in seen_ids:
                seen_ids.add(cid)
                raw_candidates.append(cand)

        # Google Books Search
        gb_results = GoogleBooksClient.search(search_title, author=search_author, limit=4)
        for cand in gb_results:
            cid = f"gb:{cand.get('external_id')}"
            if cid not in seen_ids:
                seen_ids.add(cid)
                raw_candidates.append(cand)

        # Se ainda há poucos candidatos ou baixa confiança, consulta Crossref (excelente para livros técnicos/acadêmicos)
        if len(raw_candidates) < 2:
            cr_results = CrossrefClient.search(search_title, author=search_author, limit=3)
            for cand in cr_results:
                cid = f"cr:{cand.get('external_id')}"
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    raw_candidates.append(cand)

    # 3. Detecta coerência entre múltiplas fontes (ex: Open Library + Google Books concordam em título e autor)
    for i, c1 in enumerate(raw_candidates):
        for j, c2 in enumerate(raw_candidates):
            if i != j and c1.get("source") != c2.get("source"):
                t_sim = calculate_string_similarity(c1.get("title"), c2.get("title"))
                a_sim = _calculate_author_similarity(c1.get("author"), c2.get("author"))
                if t_sim >= 0.80 and a_sim >= 0.70:
                    c1["multi_source_confirmed"] = True
                    break

    # 4. Pontuação final de todos os candidatos
    for cand in raw_candidates:
        score, conf, breakdown = score_candidate_detailed(book_meta, cand)
        cand["score"] = score
        cand["confidence_level"] = conf
        cand["score_breakdown"] = breakdown

    raw_candidates.sort(key=lambda c: c["score"], reverse=True)
    return raw_candidates
