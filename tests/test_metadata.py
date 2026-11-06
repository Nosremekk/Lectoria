"""
Testes unitários e de integração para o serviço de metadados bibliográficos (v1.4.0).
Validação de ISBNs, extração profunda de arquivos, clientes externos mockados e smart matching.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from metadata_service import (
    clean_isbn,
    validate_isbn10,
    validate_isbn13,
    isbn10_to_isbn13,
    extract_isbns_from_text,
    calculate_string_similarity,
    normalize_text_for_matching,
    extract_pdf_metadata_advanced,
    extract_epub_metadata_advanced,
    OpenLibraryClient,
    GoogleBooksClient,
    score_candidate,
    identify_book_metadata,
)
from tests.conftest import create_sample_pdf


def test_isbn_clean_and_validation():
    # ISBN-10 válido com dígito X
    valid_isbn10 = "0-8044-2957-X"
    assert clean_isbn(valid_isbn10) == "080442957X"
    assert validate_isbn10("080442957X") is True
    assert validate_isbn10("0804429570") is False

    # ISBN-13 válido
    valid_isbn13 = "978-85-359-0277-8"
    assert clean_isbn(valid_isbn13) == "9788535902778"
    assert validate_isbn13("9788535902778") is True
    assert validate_isbn13("9788535902770") is False

    # Conversão de ISBN-10 para ISBN-13
    converted = isbn10_to_isbn13("8535902775")
    assert converted == "9788535902778"
    assert validate_isbn13(converted) is True


def test_extract_isbns_from_text():
    sample_text = """
    Dados Internacionais de Catalogação na Publicação (CIP)
    Assis, Machado de, 1839-1908.
    Dom Casmurro / Machado de Assis. - São Paulo: Companhia das Letras, 2008.
    ISBN 978-85-359-0277-8
    1. Romance brasileiro. I. Título.
    Outra edição: ISBN 85-359-0277-5
    """
    found = extract_isbns_from_text(sample_text)
    assert len(found) >= 1
    isbn13_list = [f["isbn13"] for f in found]
    assert "9788535902778" in isbn13_list


def test_string_similarity_and_normalization():
    # Normalização remove acentos, pontuação e espaços múltiplos
    assert normalize_text_for_matching("Memórias Póstumas de Brás Cubas!") == "memorias postumas de bras cubas"

    # Similaridade exata
    assert calculate_string_similarity("Dom Casmurro", "dom casmurro") == 1.0

    # Similaridade com pequenas variações
    sim = calculate_string_similarity("Dom Casmurro", "Dom Casmurro: Edição Crítica")
    assert sim >= 0.6


def test_extract_pdf_metadata_advanced(tmp_path):
    pdf_file = tmp_path / "teste_cip.pdf"
    create_sample_pdf(pdf_file, "O Alienista", "Machado de Assis", pages=3)

    meta = extract_pdf_metadata_advanced(pdf_file)
    assert meta["title"] == "O Alienista"
    assert meta["author"] == "Machado de Assis"
    assert meta["page_count"] == 3


def test_candidate_scoring():
    book_meta = {
        "title": "Dom Casmurro",
        "author": "Machado de Assis",
        "isbn13": "9788535902778",
        "publish_year": 2008,
        "publisher": "Companhia das Letras",
    }

    # Candidato perfeito com mesmo ISBN
    reliable_candidate = {
        "title": "Dom Casmurro",
        "author": "Machado de Assis",
        "isbn13": "9788535902778",
        "publish_year": 2008,
        "publisher": "Companhia das Letras",
    }
    score, conf = score_candidate(book_meta, reliable_candidate)
    assert score >= 85.0
    assert conf == "reliable"

    # Candidato com título similar mas sem ISBN
    review_candidate = {
        "title": "Dom Casmurro e Outros Contos",
        "author": "Machado de Assis",
        "isbn13": None,
        "publish_year": 2015,
        "publisher": "Outra Editora",
    }
    score2, conf2 = score_candidate(book_meta, review_candidate)
    assert 50.0 <= score2 < 85.0
    assert conf2 == "needs_review"

    # Candidato totalmente diferente
    bad_candidate = {
        "title": "Física Quântica Moderna",
        "author": "Albert Einstein",
        "isbn13": "9780123456789",
    }
    score3, conf3 = score_candidate(book_meta, bad_candidate)
    assert score3 < 50.0
    assert conf3 == "insufficient"


@patch("metadata_service._safe_http_get_json")
def test_open_library_client_lookup(mock_http):
    # Simula resposta de ISBN da Open Library
    mock_http.return_value = {
        "title": "Dom Casmurro",
        "authors": [{"name": "Machado de Assis"}],
        "publishers": ["Companhia das Letras"],
        "publish_date": "2008",
        "isbn_13": ["9788535902778"],
        "isbn_10": ["8535902775"],
        "covers": [12345],
        "works": [{"key": "/works/OL123W"}],
    }

    result = OpenLibraryClient.lookup_isbn("9788535902778")
    assert result is not None
    assert result["title"] == "Dom Casmurro"
    assert result["author"] == "Machado de Assis"
    assert result["isbn13"] == "9788535902778"
    assert result["publish_year"] == 2008
    assert "covers.openlibrary.org" in result["cover_url"]


@patch("metadata_service._safe_http_get_json")
def test_google_books_client_lookup(mock_http):
    # Simula resposta de busca da Google Books API
    mock_http.return_value = {
        "totalItems": 1,
        "items": [
            {
                "id": "gb_vol_1",
                "volumeInfo": {
                    "title": "Memórias Póstumas de Brás Cubas",
                    "authors": ["Machado de Assis"],
                    "publisher": "Editora Garnier",
                    "publishedDate": "1881",
                    "description": "Obra-prima do Realismo brasileiro.",
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9788535900000"},
                    ],
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/content?id=gb_vol_1&printsec=frontcover",
                    },
                    "categories": ["Fiction / Classics"],
                },
            }
        ],
    }

    results = GoogleBooksClient.search("Memórias Póstumas", author="Machado de Assis")
    assert len(results) == 1
    first = results[0]
    assert first["title"] == "Memórias Póstumas de Brás Cubas"
    assert first["author"] == "Machado de Assis"
    assert first["source"] == "googlebooks"
    assert first["cover_url"].startswith("https://")
