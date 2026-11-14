#!/usr/bin/env python3
"""
Utilitário de Diagnóstico Somente-Leitura da Taxonomia e Facetas do Lectoria (v1.7.0).

Opera estritamente em modo somente-leitura (`?mode=ro` + `PRAGMA query_only = ON;`) para:
1. Verificar a paridade exata entre os contadores de categorias da árvore taxonômica e os livros ativos.
2. Identificar registros legados na tabela `subjects` compartilhados por múltiplos livros que possuam
   facetas `genre`, `origin` ou `period` acopladas à linha compartilhada da categoria/subcategoria.
3. Listar os livros potencialmente afetados, indicando quais possuem travas manuais (`is_manual = 1`
   ou `manual_lock_fields`) e sugerindo valores recuperáveis de forma determinística sem alterar o banco.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from taxonomy_service import map_raw_subjects_to_taxonomy


def _open_readonly_connection(db_path: Path) -> sqlite3.Connection:
    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


def inspect_taxonomy_health(db_path: Path) -> Dict[str, Any]:
    """Executa auditoria somente-leitura de contadores e facetas potencialmente sobrescritas."""
    conn = _open_readonly_connection(db_path)
    try:
        cur = conn.cursor()

        # 1. Paridade de contadores por categoria em livros ativos
        cur.execute(
            """
            SELECT
                c.id,
                c.name AS category,
                COUNT(DISTINCT b.id) AS tree_count
            FROM categories c
            JOIN books b ON (
                EXISTS (
                    SELECT 1 FROM book_categories bc
                    WHERE bc.book_id = b.id AND bc.category_id = c.id
                )
                OR (
                    NOT EXISTS (SELECT 1 FROM book_categories bc2 WHERE bc2.book_id = b.id)
                    AND EXISTS (
                        SELECT 1 FROM book_subjects bs
                        JOIN subjects s ON bs.subject_id = s.id
                        WHERE bs.book_id = b.id
                          AND TRIM(s.category) = TRIM(c.name) COLLATE NOCASE
                          AND LOWER(TRIM(s.category)) != 'geral'
                    )
                )
            )
            LEFT JOIN library_sources ls ON b.source_id = ls.id
            WHERE b.is_missing = 0 AND (ls.is_active = 1 OR ls.is_active IS NULL)
            GROUP BY c.id
            HAVING tree_count > 0
            ORDER BY c.display_order ASC, c.name COLLATE NOCASE ASC;
            """
        )
        category_counts = [dict(r) for r in cur.fetchall()]

        # 2. Identificação de assuntos legados compartilhados que carregam genre/origin/period
        cur.execute(
            """
            SELECT
                s.id AS subject_id,
                s.name AS subject_name,
                s.category,
                s.subcategory,
                s.genre,
                s.origin,
                s.period,
                COUNT(DISTINCT bs.book_id) AS linked_books_count
            FROM subjects s
            JOIN book_subjects bs ON s.id = bs.subject_id
            JOIN books b ON bs.book_id = b.id
            WHERE b.is_missing = 0
              AND (
                  (s.genre IS NOT NULL AND TRIM(s.genre) != '')
                  OR (s.origin IS NOT NULL AND TRIM(s.origin) != '')
                  OR (s.period IS NOT NULL AND TRIM(s.period) != '')
              )
              AND s.name NOT LIKE '% — Gênero — %'
              AND s.name NOT LIKE '% — Origem — %'
              AND s.name NOT LIKE '% — Período — %'
            GROUP BY s.id
            HAVING linked_books_count > 1
            ORDER BY linked_books_count DESC, s.name ASC;
            """
        )
        shared_contaminated_subjects = [dict(r) for r in cur.fetchall()]

        affected_books: List[Dict[str, Any]] = []
        if shared_contaminated_subjects:
            subj_ids = [int(s["subject_id"]) for s in shared_contaminated_subjects]
            placeholders = ",".join("?" for _ in subj_ids)
            cur.execute(
                f"""
                SELECT DISTINCT
                    b.id AS book_id,
                    b.title,
                    b.author,
                    b.description,
                    b.language,
                    b.manual_lock_fields,
                    bs.subject_id,
                    bs.is_manual AS subject_is_manual,
                    (SELECT MAX(bc.is_manual) FROM book_categories bc WHERE bc.book_id = b.id) AS category_is_manual
                FROM books b
                JOIN book_subjects bs ON b.id = bs.book_id
                WHERE bs.subject_id IN ({placeholders}) AND b.is_missing = 0
                ORDER BY b.title ASC;
                """,
                subj_ids,
            )
            for row in cur.fetchall():
                r = dict(row)
                locks = []
                if r.get("manual_lock_fields"):
                    try:
                        locks = json.loads(r["manual_lock_fields"])
                    except Exception:
                        locks = []
                has_manual_classification = bool(
                    r.get("subject_is_manual")
                    or r.get("category_is_manual")
                    or ("categories" in locks)
                )
                inferred = map_raw_subjects_to_taxonomy(
                    raw_subjects=[],
                    title=r.get("title") or "",
                    author=r.get("author") or "",
                    description=r.get("description") or "",
                    language=r.get("language") or "",
                )
                affected_books.append(
                    {
                        "book_id": r["book_id"],
                        "title": r["title"],
                        "author": r["author"],
                        "shared_subject_id": r["subject_id"],
                        "has_manual_classification": has_manual_classification,
                        "suggested_genre": inferred.get("genre"),
                        "suggested_origin": inferred.get("origin"),
                        "suggested_period": inferred.get("period"),
                    }
                )

        return {
            "db_path": str(Path(db_path).resolve()),
            "mode": "read_only",
            "categories_summary": category_counts,
            "shared_legacy_facet_subjects_count": len(shared_contaminated_subjects),
            "shared_legacy_facet_subjects": shared_contaminated_subjects,
            "potentially_affected_books_count": len(affected_books),
            "potentially_affected_books": affected_books,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico somente-leitura da taxonomia e facetas do Lectoria."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(config.DB_PATH),
        help="Caminho para o arquivo SQLite (padrão: config.DB_PATH).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Exibe o relatório em formato JSON estruturado.",
    )
    args = parser.parse_args()

    report = inspect_taxonomy_health(Path(args.db))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== Diagnóstico Taxonômico Somente-Leitura ({report['db_path']}) ===")
        print(f"Categorias ativas com livros: {len(report['categories_summary'])}")
        for c in report["categories_summary"]:
            print(f"  - {c['category']}: {c['tree_count']} livro(s)")
        print(
            f"Assuntos compartilhados legados com genre/origin/period acoplados: "
            f"{report['shared_legacy_facet_subjects_count']}"
        )
        print(
            f"Livros potencialmente afetados: {report['potentially_affected_books_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
