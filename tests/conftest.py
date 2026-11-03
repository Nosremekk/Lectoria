"""
Configurações e fixtures para testes do Lectoria.
"""

import os
import sys
import shutil
import tempfile
import pytest
from pathlib import Path

# Adiciona o diretório raiz ao PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
import database
import library
from main import app
from starlette.testclient import TestClient
import pypdf
from pypdf import PdfWriter


def create_sample_pdf(path: Path, title: str, author: str, pages: int = 5):
    """Cria um PDF válido para testes com metadados."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    writer.add_metadata({
        "/Title": title,
        "/Author": author,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="session", autouse=True)
def test_env():
    """Configura o ambiente de teste isolado para toda a sessão."""
    tmp_path = Path(tempfile.mkdtemp(prefix="lectoria_test_"))
    test_lib = tmp_path / "books"
    test_data = tmp_path / "data"
    test_lib.mkdir(parents=True, exist_ok=True)
    test_data.mkdir(parents=True, exist_ok=True)

    # Configura variáveis globais no módulo config
    config.BASE_DIR = tmp_path
    config.LIBRARY_DIR = test_lib
    config.DATA_DIR = test_data
    config.DB_PATH = test_data / "lectoria.db"
    config.COVERS_DIR = test_data / "covers"
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    test_backups = tmp_path / "backups"
    test_backups.mkdir(parents=True, exist_ok=True)
    config.BACKUPS_DIR = test_backups

    # Cria tabelas no banco de teste
    database.init_db()

    # Cria acervo inicial
    create_sample_pdf(test_lib / "dom_casmurro.pdf", "Dom Casmurro", "Machado de Assis", pages=10)
    create_sample_pdf(test_lib / "memorias_postumas.pdf", "Memórias Póstumas", "Machado de Assis", pages=15)
    create_sample_pdf(test_lib / "ficcoes.pdf", "Ficções", "Jorge Luis Borges", pages=8)

    # Varre a biblioteca inicial
    library.scan_library()

    yield {
        "root": tmp_path,
        "library_dir": test_lib,
        "data_dir": test_data,
        "db_path": config.DB_PATH,
        "backups_dir": test_backups,
    }

    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
def client():
    """Cliente HTTP de teste com o ciclo de vida completo do FastAPI."""
    with TestClient(app) as c:
        yield c
