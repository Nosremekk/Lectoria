"""
Módulo de gerenciamento de fontes de biblioteca do Lectoria.
Permite cadastrar, listar, ativar, desativar e remover diretórios locais como fontes de PDFs.
Aplica validações rigorosas de segurança para impedir inclusão de diretórios do sistema ou internos do projeto.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from database import get_db


class SourceError(Exception):
    """Exceção levantada em caso de erro na configuração ou validação de fontes."""
    pass


class InvalidSourceError(SourceError):
    """Exceção levantada quando o caminho da fonte é inválido ou proibido."""
    pass


class SourceNotFoundError(Exception):
    """Exceção levantada quando uma fonte não for encontrada."""
    pass


# Diretórios críticos do sistema operacional que nunca podem ser cadastrados como fontes
FORBIDDEN_SYSTEM_PATHS = {
    Path("/"),
    Path("/bin"),
    Path("/sbin"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/lib"),
    Path("/lib64"),
    Path("/proc"),
    Path("/root"),
    Path("/sys"),
    Path("/usr/bin"),
    Path("/usr/sbin"),
}


def validate_and_canonicalize_path(raw_path: str) -> Path:
    """
    Valida e normaliza o caminho para uma fonte da biblioteca.
    Rejeita caminhos inexistentes, não-diretórios, caminhos de sistema críticos e pastas internas da aplicação.
    """
    if not raw_path or not raw_path.strip():
        raise InvalidSourceError("O caminho do diretório não pode ser vazio.")

    clean_str = raw_path.strip()
    target_path = Path(clean_str).expanduser()

    try:
        resolved = target_path.resolve()
    except Exception as e:
        raise InvalidSourceError(f"Caminho inválido: {e}")

    if not resolved.exists():
        raise InvalidSourceError(f"O diretório '{resolved}' não existe no sistema de arquivos.")

    if not resolved.is_dir():
        raise InvalidSourceError(f"O caminho '{resolved}' não é um diretório.")

    # 1. Bloqueio de caminhos críticos do sistema operacional
    if resolved == Path("/"):
        raise InvalidSourceError("O diretório raiz '/' não pode ser cadastrado como fonte de biblioteca.")

    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if forbidden == Path("/"):
            continue
        if resolved == forbidden or forbidden in resolved.parents:
            raise InvalidSourceError(f"Acesso negado: '{resolved}' é um diretório reservado do sistema operacional.")

    # 2. Bloqueio de diretórios internos da aplicação (dados, backups e raiz do código-fonte)
    data_dir = config.DATA_DIR.resolve()
    backups_dir = config.BACKUPS_DIR.resolve()
    base_dir = config.BASE_DIR.resolve()

    # O diretório padrão de livros books/ é permitido, mas a raiz da aplicação base_dir, data_dir e backups_dir são proibidos
    if resolved == base_dir:
        raise InvalidSourceError("A raiz do projeto Lectoria não pode ser cadastrada como fonte de biblioteca.")

    if resolved == data_dir or data_dir in resolved.parents:
        raise InvalidSourceError("O diretório de banco de dados e dados internos da aplicação não pode ser uma fonte.")

    if resolved == backups_dir or backups_dir in resolved.parents:
        raise InvalidSourceError("O diretório de backups da aplicação não pode ser cadastrado como fonte de biblioteca.")

    # Verifica permissão de leitura
    if not os.access(resolved, os.R_OK):
        raise InvalidSourceError(f"Sem permissão de leitura no diretório '{resolved}'.")

    return resolved


def add_library_source(name: str = "", path: str = "", label: Optional[str] = None) -> Dict[str, Any]:
    """Cadastra um novo diretório local como fonte de livros da biblioteca."""
    effective_name = (name or label or "").strip()
    if not effective_name:
        raise ValueError("O nome amigável da fonte não pode ser vazio.")

    canonical_path = validate_and_canonicalize_path(path)
    path_str = str(canonical_path)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM library_sources WHERE path = ?", (path_str,))
        if cursor.fetchone():
            raise SourceError(f"O diretório '{path_str}' já está cadastrado como fonte da biblioteca.")

        cursor.execute(
            """
            INSERT INTO library_sources (name, path, is_active)
            VALUES (?, ?, 1)
            """,
            (effective_name, path_str),
        )
        source_id = cursor.lastrowid
        return {
            "id": source_id,
            "name": effective_name,
            "label": effective_name,
            "path": path_str,
            "is_active": 1,
            "book_count": 0,
        }


def list_library_sources() -> List[Dict[str, Any]]:
    """Lista todas as fontes cadastradas com contagem de livros associados e status."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                s.id,
                s.name,
                s.path,
                s.is_active,
                s.created_at,
                s.last_scanned_at,
                s.last_error,
                COUNT(b.id) as book_count
            FROM library_sources s
            LEFT JOIN books b ON s.id = b.source_id AND b.is_missing = 0
            GROUP BY s.id
            ORDER BY s.id ASC
            """
        )
        sources = []
        for r in cursor.fetchall():
            d = dict(r)
            # Verifica acessibilidade em tempo real
            p = Path(d["path"])
            d["is_accessible"] = p.exists() and p.is_dir() and os.access(p, os.R_OK)
            sources.append(d)
        return sources


def get_library_source(source_id: int) -> Dict[str, Any]:
    """Retorna detalhes de uma fonte específica."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                s.id,
                s.name,
                s.path,
                s.is_active,
                s.created_at,
                s.last_scanned_at,
                s.last_error,
                COUNT(b.id) as book_count
            FROM library_sources s
            LEFT JOIN books b ON s.id = b.source_id AND b.is_missing = 0
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (source_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise SourceNotFoundError(f"Fonte #{source_id} não encontrada.")
        d = dict(row)
        p = Path(d["path"])
        d["is_accessible"] = p.exists() and p.is_dir() and os.access(p, os.R_OK)
        return d


def set_source_active(source_id: int, is_active: bool) -> Dict[str, Any]:
    """Ativa ou desativa uma fonte de biblioteca."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM library_sources WHERE id = ?", (source_id,))
        if not cursor.fetchone():
            raise SourceNotFoundError(f"Fonte #{source_id} não encontrada.")

        cursor.execute(
            "UPDATE library_sources SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, source_id),
        )
    return get_library_source(source_id)


def delete_library_source(source_id: int) -> bool:
    """
    Remove uma fonte da configuração do Lectoria.
    NUNCA exclui os arquivos PDFs originais do sistema de arquivos.
    Apenas remove o cadastro da fonte e os registros de livros associados no catálogo.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM library_sources WHERE id = ?", (source_id,))
        row = cursor.fetchone()
        if not row:
            raise SourceNotFoundError(f"Fonte #{source_id} não encontrada.")

        # Remove os livros associados a essa fonte do catálogo (arquivos físicos permanecem intactos)
        cursor.execute("DELETE FROM books WHERE source_id = ?", (source_id,))
        cursor.execute("DELETE FROM library_sources WHERE id = ?", (source_id,))
        return True


def toggle_source_active(source_id: int) -> Dict[str, Any]:
    """Alterna o status ativo/inativo de uma fonte."""
    src = get_library_source(source_id)
    return set_source_active(source_id, not src["is_active"])


# Aliases para compatibilidade de nomenclatura
list_sources = list_library_sources
create_source = add_library_source
delete_source = delete_library_source
