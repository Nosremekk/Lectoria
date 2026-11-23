"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Conceitos (`knowledge_service.py`).

Cobre todos os cenários da Seção 14.2:
- Criação de conceito válido.
- Rejeição de nome vazio, apenas espaços ou acima do limite.
- Normalização consistente de nome (NFC + casefold).
- Bloqueio de conceito duplicado para o mesmo usuário.
- Permissão de conceitos com mesmo nome para usuários diferentes.
- Atualização de nome preservando UUID e conexões existentes.
- Atualização de descrição, tags, cor e estado arquivado.
- Exclusão de conceito próprio e limpeza de vínculos.
- Bloqueio de acesso, edição ou exclusão por outro usuário.
- Paginação e ordenação determinística.
"""

from pathlib import Path
import shutil
import tempfile
import unicodedata
import pytest

import config
import database
import knowledge_service as ks


@pytest.fixture
def knowledge_env(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_concepts_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "obra1.pdf").write_bytes(b"%PDF-1.4 dummy")

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
            (20, 'bob', 'scrypt$b', 'reader', 'Bob', 1),
            (30, 'admin', 'scrypt$adm', 'admin', 'Admin', 1),
            (40, 'inativo', 'scrypt$i', 'reader', 'Inativo', 0);
            """
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('book-1', 1, 'A Técnica', 'Jacques Ellul', 'obra1.pdf', 'obra1.pdf', 100, 'pdf', 100, 0);
            """
        )

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "alice": 10, "bob": 20, "admin": 30, "inactive": 40}
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_create_concept_valid_and_normalization(knowledge_env):
    """Cria conceito válido, normaliza nome em NFC, deduplica tags e aplica cor."""
    alice = knowledge_env["alice"]
    # Usa string NFD decomposta para validar normalização automática em NFC
    nfd_name = unicodedata.normalize("NFD", "  Técnica   Moderna  ")
    c = ks.create_concept(
        alice,
        name=nfd_name,
        description="Estudo sobre a autonomia da técnica.",
        tags=["#filosofia", "Filosofia", "modernidade"],
        color="#D4A853",
    )
    assert c["id"]
    assert c["user_id"] == alice
    assert c["name"] == unicodedata.normalize("NFC", "Técnica Moderna")
    assert c["normalized_name"] == unicodedata.normalize("NFC", "técnica moderna")
    assert c["tags"] == ["filosofia", "modernidade"]
    assert c["color"] == "#d4a853"
    assert c["is_archived"] is False
    assert c["links_count"] == 0


def test_concept_validation_rejects_invalid_inputs(knowledge_env):
    """Rejeita nome vazio, espaços, acima do limite, descrição longa, cor inválida ou tags excessivas."""
    alice = knowledge_env["alice"]

    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="")
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="    \t\n  ")
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="A" * (ks.MAX_CONCEPT_NAME_LEN + 1))
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="Válido", description="D" * (ks.MAX_CONCEPT_DESC_LEN + 1))
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="Válido", color="not-a-color")
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_concept(alice, name="Válido", tags=[f"tag-{i}" for i in range(ks.MAX_TAGS_COUNT + 1)])


def test_duplicate_concept_blocked_same_user_allowed_different_users(knowledge_env):
    """Bloqueia nome duplicado (inclusive case-insensitive ou NFC vs NFD) no mesmo usuário, mas permite em outro."""
    alice = knowledge_env["alice"]
    bob = knowledge_env["bob"]

    ks.create_concept(alice, name="Racionalização")

    with pytest.raises(ks.KnowledgeConflictError):
        ks.create_concept(alice, name="racionalização")

    with pytest.raises(ks.KnowledgeConflictError):
        ks.create_concept(alice, name=unicodedata.normalize("NFD", "RACIONALIZAÇÃO"))

    # Bob pode criar um conceito com exatamente o mesmo nome
    c_bob = ks.create_concept(bob, name="Racionalização", description="Conceito privado do Bob")
    assert c_bob["user_id"] == bob


def test_update_concept_preserves_uuid_and_existing_links(knowledge_env):
    """Renomear e atualizar descrição/tags/cor/arquivamento preserva o UUID e vínculos existentes."""
    alice = knowledge_env["alice"]

    c = ks.create_concept(alice, name="Poder", description="Versão 1", tags=["foucault"])
    cid = c["id"]

    # Cria uma conexão vinculada ao conceito
    link = ks.create_knowledge_link(
        alice,
        source_type="concept",
        source_id=cid,
        target_type="book",
        target_id="book-1",
        relation_type="references",
        rationale="Referência base",
    )

    updated = ks.update_concept(
        cid,
        alice,
        name="Poder Disciplinar",
        description="Versão 2 ampliada",
        tags=["foucault", "vigilância"],
        color="#2563eb",
        is_archived=True,
    )
    assert updated["id"] == cid
    assert updated["name"] == "Poder Disciplinar"
    assert updated["normalized_name"] == "poder disciplinar"
    assert updated["description"] == "Versão 2 ampliada"
    assert updated["tags"] == ["foucault", "vigilância"]
    assert updated["color"] == "#2563eb"
    assert updated["is_archived"] is True
    assert updated["links_count"] == 1

    # A conexão continua apontando para o mesmo UUID e resolve o novo nome
    fetched_link = ks.get_knowledge_link(link["id"], alice)
    assert fetched_link["source"]["id"] == cid
    assert fetched_link["source"]["label"] == "Poder Disciplinar"


def test_delete_concept_and_isolation_between_users(knowledge_env):
    """Testa bloqueio de acesso/edição/exclusão por terceiros (inclusive admin) e limpeza ao excluir."""
    alice = knowledge_env["alice"]
    bob = knowledge_env["bob"]
    admin = knowledge_env["admin"]

    c = ks.create_concept(alice, name="Alienação")
    cid = c["id"]
    ks.create_knowledge_link(alice, "concept", cid, "book", "book-1", "relates_to")

    # Bob e Admin não podem ler, editar ou excluir conceito privado da Alice
    for other_uid in (bob, admin):
        with pytest.raises(ks.KnowledgeNotFoundError):
            ks.get_concept(cid, other_uid)
        with pytest.raises(ks.KnowledgeNotFoundError):
            ks.update_concept(cid, other_uid, description="Invasão")
        with pytest.raises(ks.KnowledgeNotFoundError):
            ks.delete_concept(cid, other_uid)

    # Alice exclui seu conceito
    res = ks.delete_concept(cid, alice)
    assert res == {"deleted": True, "id": cid}

    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.get_concept(cid, alice)

    # Conexão associada ao conceito foi limpa pelo trigger
    assert ks.list_outgoing_links("concept", cid, alice) == []


def test_list_concepts_pagination_sorting_and_accent_search(knowledge_env):
    """Valida paginação, ordenação determinística, filtro de arquivados e busca auxiliar com/sem acentos."""
    alice = knowledge_env["alice"]

    ks.create_concept(alice, name="Ética", description="Filosofia moral", tags=["moral"])
    ks.create_concept(alice, name="Automação", description="Processos técnicos", tags=["tecnologia"])
    ks.create_concept(alice, name="Burocracia", description="Racionalização administrativa", tags=["sociologia"])
    ks.create_concept(alice, name="Conceito Arquivado", is_archived=True)

    # Por padrão não inclui arquivados
    listed = ks.list_concepts(alice, sort_by="name_asc", limit=2, offset=0)
    assert listed["total"] == 3
    assert [c["name"] for c in listed["items"]] == ["Automação", "Burocracia"]

    listed_p2 = ks.list_concepts(alice, sort_by="name_asc", limit=2, offset=2)
    assert [c["name"] for c in listed_p2["items"]] == ["Ética"]

    # Busca insensível a acentos
    lookup = ks.search_concepts_for_lookup(alice, query="etica")
    assert len(lookup) == 1
    assert lookup[0]["name"] == "Ética"
