"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Reflexões Autorais (`knowledge_service.py`).

Cobre todos os cenários da Seção 14.3:
- Criação de reflexão válida.
- Criação com `primary_book_id` válido.
- Rejeição de `primary_book_id` inexistente ou inacessível (fonte inativa / livro ausente).
- Rejeição de título vazio ou acima do limite.
- Rejeição de `body_markdown` acima do limite.
- Atualização de título, corpo, tags, fixação e arquivamento.
- Exclusão de reflexão própria e limpeza de conexões.
- Bloqueio de acesso, edição ou exclusão por outro usuário.
- Consulta de referências estruturadas e backlinks de uma reflexão.
"""

from pathlib import Path
import shutil
import tempfile
import pytest

import config
import database
import knowledge_service as ks


@pytest.fixture
def reflection_env(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_refl_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "ellul.pdf").write_bytes(b"%PDF-1.4 dummy")
    (lib_dir / "inativo.pdf").write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", lib_dir)

    database.init_db(db_path)

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
            (10, 'alice', 'scrypt$a', 'reader', 'Alice', 1),
            (20, 'bob', 'scrypt$b', 'reader', 'Bob', 1);
            """
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Ativa', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (2, 'Inativa', ?, 0);",
            (str(lib_dir / "inativa"),),
        )
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing) VALUES
            ('book-active', 1, 'A Técnica e o Desafio', 'Jacques Ellul', 'ellul.pdf', 'ellul.pdf', 100, 'pdf', 200, 0),
            ('book-inactive-src', 2, 'Livro Fonte Inativa', 'Autor', 'inativo.pdf', 'inativo.pdf', 100, 'pdf', 100, 0),
            ('book-missing', 1, 'Livro Ausente Disco', 'Autor', 'ausente.pdf', 'ausente.pdf', 100, 'pdf', 100, 1);
            """
        )
        cur.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id, visibility)
            VALUES ('hl-alice-1', 'book-active', 15, '#fef08a', '[]', 'A técnica cria seu próprio meio.', 'Cap. 1', 10, 'private');
            """
        )

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "alice": 10, "bob": 20}
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_create_reflection_valid_with_and_without_primary_book(reflection_env):
    """Cria reflexão válida sem livro principal e com `primary_book_id` válido."""
    alice = reflection_env["alice"]

    r1 = ks.create_reflection(
        alice,
        title="Notas sobre Filosofia da Técnica",
        body_markdown="# Introdução\n\nSíntese comparativa entre Ellul e Heidegger.",
        tags=["ensaio", "filosofia"],
        is_pinned=True,
    )
    assert r1["id"]
    assert r1["user_id"] == alice
    assert r1["title"] == "Notas sobre Filosofia da Técnica"
    assert "# Introdução" in r1["body_markdown"]
    assert r1["primary_book_id"] is None
    assert r1["tags"] == ["ensaio", "filosofia"]
    assert r1["is_pinned"] is True
    assert r1["is_archived"] is False

    r2 = ks.create_reflection(
        alice,
        title="Fichamento de Ellul",
        body_markdown="Capítulo 1 analisa o fenômeno técnico.",
        primary_book_id="book-active",
    )
    assert r2["primary_book_id"] == "book-active"
    assert r2["primary_book_title"] == "A Técnica e o Desafio"
    assert r2["primary_book_available"] is True


def test_reflection_rejects_invalid_primary_book_and_invalid_lengths(reflection_env):
    """Rejeita primary_book_id inexistente, ausente ou de fonte inativa, bem como títulos/corpos inválidos."""
    alice = reflection_env["alice"]

    for bad_book in ("book-nonexistent", "book-inactive-src", "book-missing"):
        with pytest.raises(ks.KnowledgeNotFoundError):
            ks.create_reflection(
                alice,
                title="Reflexão com livro inválido",
                body_markdown="Texto",
                primary_book_id=bad_book,
            )

    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_reflection(alice, title="   ", body_markdown="Texto")

    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_reflection(alice, title="T" * (ks.MAX_REFLECTION_TITLE_LEN + 1), body_markdown="Texto")

    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_reflection(alice, title="Título válido", body_markdown="B" * (ks.MAX_REFLECTION_BODY_LEN + 1))


def test_update_and_delete_reflection_and_multiuser_isolation(reflection_env):
    """Testa atualização, exclusão e isolamento estrito entre usuários."""
    alice = reflection_env["alice"]
    bob = reflection_env["bob"]

    r = ks.create_reflection(alice, title="Rascunho 1", body_markdown="Texto inicial")
    rid = r["id"]

    # Bob não pode acessar, editar ou excluir reflexão privada da Alice
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.get_reflection(rid, bob)
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.update_reflection(rid, bob, title="Adulterado")
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.delete_reflection(rid, bob)

    updated = ks.update_reflection(
        rid,
        alice,
        title="Ensaio Finalizado",
        body_markdown="Texto revisado e expandido.",
        primary_book_id="book-active",
        tags=["concluído"],
        is_pinned=True,
        is_archived=True,
    )
    assert updated["id"] == rid
    assert updated["title"] == "Ensaio Finalizado"
    assert updated["body_markdown"] == "Texto revisado e expandido."
    assert updated["primary_book_id"] == "book-active"
    assert updated["tags"] == ["concluído"]
    assert updated["is_pinned"] is True
    assert updated["is_archived"] is True

    # Associa a um livro e depois exclui a reflexão
    ks.create_knowledge_link(alice, "reflection", rid, "book", "book-active", "references")
    assert len(ks.get_reflection_references(rid, alice)) == 1

    ks.delete_reflection(rid, alice)
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.get_reflection(rid, alice)
    assert ks.list_outgoing_links("reflection", rid, alice) == []


def test_reflection_references_and_backlinks(reflection_env):
    """Valida consulta de referências que saem da reflexão e backlinks que apontam para ela."""
    alice = reflection_env["alice"]

    concept = ks.create_concept(alice, name="Autonomia Técnica")
    refl_base = ks.create_reflection(alice, title="Reflexão Base", body_markdown="Articula destaque e conceito.")
    refl_meta = ks.create_reflection(alice, title="Reflexão Posterior", body_markdown="Desenvolve a reflexão base.")

    # refl_base referencia hl-alice-1 e fundamenta concept
    ks.create_knowledge_link(
        alice,
        source_type="reflection",
        source_id=refl_base["id"],
        target_type="highlight",
        target_id="hl-alice-1",
        relation_type="references",
        rationale="Citação central da página 15",
    )
    ks.create_knowledge_link(
        alice,
        source_type="reflection",
        source_id=refl_base["id"],
        target_type="concept",
        target_id=concept["id"],
        relation_type="grounds",
    )

    # refl_meta desenvolve refl_base (gera um backlink em refl_base)
    ks.create_knowledge_link(
        alice,
        source_type="reflection",
        source_id=refl_meta["id"],
        target_type="reflection",
        target_id=refl_base["id"],
        relation_type="develops",
        rationale="Amplia a hipótese para o século XXI",
    )

    refs = ks.get_reflection_references(refl_base["id"], alice)
    assert len(refs) == 2
    target_types = {r["target_type"] for r in refs}
    assert target_types == {"highlight", "concept"}

    backlinks = ks.get_reflection_backlinks(refl_base["id"], alice)
    assert len(backlinks) == 1
    assert backlinks[0]["source_id"] == refl_meta["id"]
    assert backlinks[0]["direction"] == "incoming"
    assert backlinks[0]["relation_label"] == "Desenvolvido por"

    detail = ks.get_reflection(refl_base["id"], alice)
    assert detail["references_count"] == 2
    assert detail["backlinks_count"] == 1
