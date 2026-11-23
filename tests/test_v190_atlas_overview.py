"""
Suíte de Testes Unitários, de Integração de API e Escala Quantitativa — Fase 5 da Lectoria v1.9.0.
Valida:
- GET /api/knowledge/summary (visão geral agregada do Atlas, contagens exatas, isolamento multiusuário,
  truncamento de excerpt de reflexões sem vazar body_markdown completo, 401 e Cache-Control: private, no-store).
- Verificação quantitativa determinística (Seção 24.1) com 1.000 conceitos, 1.000 reflexões e 10.000 conexões.
"""

import json
import time
import uuid

import annotations_service
import auth_service
import config
import database
import knowledge_service


def _auth_cookies(token: str, csrf: str = "csrf_p5_unit"):
    return {"lectoria_session": token, "lectoria_csrf": csrf}


def _seed_book(book_id: str, title: str):
    lib_dir = config.LIBRARY_DIR
    fname = f"{book_id}.pdf"
    fpath = lib_dir / fname
    if not fpath.exists():
        fpath.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with database.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Fonte P5', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES (?, ?, 'Autor P5', ?, ?, ?, 'pdf', 10, 1, 0);
            """,
            (book_id, title, fname, fname, fpath.stat().st_size),
        )


def test_v190_atlas_overview_summary_accuracy_and_isolation(client):
    _seed_book("book_p5_ov", "Obra Visão Geral Atlas")

    alice = auth_service.create_user("alice_p5_ov", "Password123!", "Alice Atlas", role="admin")
    bob = auth_service.create_user("bob_p5_ov", "Password123!", "Bob Atlas", role="reader")
    tok_a = auth_service.create_session(alice["id"])
    tok_b = auth_service.create_session(bob["id"])

    # Sem autenticação -> 401
    client.cookies.clear()
    assert client.get("/api/knowledge/summary").status_code == 401

    # Estado vazio inicial para Alice
    client.cookies.update(_auth_cookies(tok_a))
    r_empty = client.get("/api/knowledge/summary")
    assert r_empty.status_code == 200
    assert r_empty.headers.get("cache-control") == "private, no-store"
    d_empty = r_empty.json()
    assert d_empty["concepts_count"] == 0
    assert d_empty["reflections_count"] == 0
    assert d_empty["links_count"] == 0
    assert d_empty["connected_books_count"] == 0
    assert d_empty["recent_concepts"] == []
    assert d_empty["recent_reflections"] == []

    # Alice cria 2 conceitos (1 ativo, 1 arquivado), 2 reflexões (1 fixada com corpo longo, 1 arquivada) e 2 conexões
    c1 = knowledge_service.create_concept(alice["id"], "Epistemologia Crítica", description="Estudo dos limites.")
    knowledge_service.create_concept(alice["id"], "Conceito Arquivado", is_archived=True)

    long_md = "# Ensaio\n\n" + ("Texto autoral extenso de reflexão filosófica. " * 25)
    ref1 = knowledge_service.create_reflection(
        alice["id"],
        "Síntese sobre Técnica e Método",
        long_md,
        primary_book_id="book_p5_ov",
        tags=["ensaio"],
        is_pinned=True,
    )
    knowledge_service.create_reflection(alice["id"], "Reflexão Arquivada", "Corpo arquivado", is_archived=True)

    hl = annotations_service.create_highlight(
        book_id="book_p5_ov",
        user_id=alice["id"],
        page=2,
        selected_text="Destaque conectado ao Atlas.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        visibility="public",
    )
    knowledge_service.create_knowledge_link(alice["id"], "reflection", ref1["id"], "concept", c1["id"], "develops")
    knowledge_service.create_knowledge_link(alice["id"], "reflection", ref1["id"], "highlight", hl["id"], "references")

    # Bob cria dados próprios que jamais devem aparecer no resumo de Alice
    cb = knowledge_service.create_concept(bob["id"], "Conceito Privado de Bob")
    rb = knowledge_service.create_reflection(bob["id"], "Reflexão Privada de Bob", "Segredo de Bob")
    knowledge_service.create_knowledge_link(bob["id"], "reflection", rb["id"], "concept", cb["id"], "relates_to")

    r_alice = client.get("/api/knowledge/summary")
    assert r_alice.status_code == 200
    da = r_alice.json()
    assert da["concepts_count"] == 1
    assert da["archived_concepts_count"] == 1
    assert da["reflections_count"] == 1
    assert da["pinned_reflections_count"] == 1
    assert da["links_count"] == 2
    assert da["connected_books_count"] == 1
    assert len(da["recent_concepts"]) == 1
    assert da["recent_concepts"][0]["name"] == "Epistemologia Crítica"
    assert da["recent_concepts"][0]["links_count"] == 1
    assert len(da["recent_reflections"]) == 1
    rec_ref = da["recent_reflections"][0]
    assert rec_ref["title"] == "Síntese sobre Técnica e Método"
    assert rec_ref["references_count"] == 2
    assert len(rec_ref["excerpt"]) <= 200
    assert "body_markdown" not in rec_ref

    # Verifica isolamento para Bob
    client.cookies.clear()
    client.cookies.update(_auth_cookies(tok_b))
    db = client.get("/api/knowledge/summary").json()
    assert db["concepts_count"] == 1
    assert db["reflections_count"] == 1
    assert db["links_count"] == 1
    assert db["recent_concepts"][0]["name"] == "Conceito Privado de Bob"


def test_v190_phase5_quantitative_scale_1k_concepts_1k_reflections_10k_links():
    """
    Validação quantitativa determinística (Seção 24.1):
    1.000 conceitos, 1.000 reflexões e 10.000 conexões para um único usuário.
    """
    _seed_book("book_p5_scale", "Livro Escala Fase 5")
    u = auth_service.create_user("user_p5_scale", "Password123!", "Leitor Escala", role="reader")
    uid = u["id"]
    now = "2026-11-19 15:00:00"

    concept_ids = [str(uuid.uuid4()) for _ in range(1000)]
    reflection_ids = [str(uuid.uuid4()) for _ in range(1000)]

    with database.get_db() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '["escala"]', '#d4a853', 0, ?, ?);
            """,
            [(cid, uid, f"Conceito Escala {i:04d}", f"conceito escala {i:04d}", f"Descrição {i}", now, now) for i, cid in enumerate(concept_ids)],
        )
        cur.executemany(
            """
            INSERT INTO reflections (id, user_id, title, body_markdown, primary_book_id, tags_json, is_pinned, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'book_p5_scale', '["ensaio"]', 0, 0, ?, ?);
            """,
            [(rid, uid, f"Reflexão Escala {i:04d}", f"# Reflexão {i}\n\nTexto sintético de avaliação de desempenho.", now, now) for i, rid in enumerate(reflection_ids)],
        )
        # 10.000 conexões (cada reflexão ligada a 10 conceitos distintos)
        link_rows = []
        for r_idx, rid in enumerate(reflection_ids):
            for offset in range(10):
                cid = concept_ids[(r_idx + offset) % 1000]
                link_rows.append((str(uuid.uuid4()), uid, "reflection", rid, "concept", cid, "references", "Justificativa sintética", now, now))
        cur.executemany(
            """
            INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            link_rows,
        )

    t0 = time.perf_counter()
    ov = knowledge_service.get_atlas_overview_summary(uid, recent_limit=6)
    t_ov_ms = (time.perf_counter() - t0) * 1000
    ov_bytes = len(json.dumps(ov, ensure_ascii=False).encode("utf-8"))

    t1 = time.perf_counter()
    c_page = knowledge_service.list_concepts(uid, limit=24, offset=0)
    t_concepts_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    r_page = knowledge_service.list_reflections(uid, limit=24, offset=0)
    t_reflections_ms = (time.perf_counter() - t2) * 1000

    t3 = time.perf_counter()
    l_page = knowledge_service.list_knowledge_links(uid, limit=24, offset=0)
    t_links_ms = (time.perf_counter() - t3) * 1000

    assert ov["concepts_count"] == 1000
    assert ov["reflections_count"] == 1000
    assert ov["links_count"] == 10000
    assert ov["connected_books_count"] == 1
    assert ov_bytes < 16384
    assert c_page["total"] == 1000 and len(c_page["items"]) == 24 and c_page["has_more"] is True
    assert r_page["total"] == 1000 and len(r_page["items"]) == 24 and r_page["has_more"] is True
    assert l_page["total"] == 10000 and len(l_page["items"]) == 24 and l_page["has_more"] is True

    assert t_ov_ms < 1000
    assert t_concepts_ms < 1000
    assert t_reflections_ms < 1000
    assert t_links_ms < 1000


def test_v190_phase51_atomic_optimistic_concurrency_and_all_five_entity_type_filters(client):
    """
    Validações regressivas da Fase 5.1 (Seções 6 e 8):
    1. Controle otimista de concorrência atômico no backend via `expected_updated_at` em
       PATCH /api/knowledge/reflections/{id}, com resolução de microssegundos (sem sleep).
    2. Suporte funcional completo aos 5 tipos aprovados de entidade (`concept`, `reflection`,
       `highlight`, `note`, `book`) como filtro `entity_type` em GET /api/knowledge/links.
    """
    _seed_book("book_p51_test", "Obra Fase 5.1")
    alice = auth_service.create_user("alice_p51", "Password123!", "Alice P5.1", role="reader")
    tok_a = auth_service.create_session(alice["id"])
    csrf = "csrf_p51_token"
    client.cookies.clear()
    client.cookies.update(_auth_cookies(tok_a, csrf))
    headers = {"x-csrf-token": csrf}

    # 1. Cria reflexão e simula duas sessões lendo a mesma versão inicial (ts_v1)
    ref_v1 = client.post(
        "/api/knowledge/reflections",
        json={"title": "Reflexão Concorrente v1", "body_markdown": "Texto inicial v1"},
        headers=headers,
    ).json()["reflection"]
    ts_v1 = ref_v1["updated_at"]

    # Sessão B salva imediatamente (no mesmo segundo, sem sleep) enviando expected_updated_at = ts_v1
    res_b = client.patch(
        f"/api/knowledge/reflections/{ref_v1['id']}",
        json={
            "title": "Reflexão Editada pela Sessão B",
            "body_markdown": "Texto salvo pela Sessão B",
            "expected_updated_at": ts_v1,
        },
        headers=headers,
    )
    assert res_b.status_code == 200
    ts_v2 = res_b.json()["reflection"]["updated_at"]
    assert ts_v2 != ts_v1

    # Sessão A tenta salvar com expected_updated_at obsoleto (ts_v1) -> deve receber HTTP 409 Conflict atômico!
    res_a_conflict = client.patch(
        f"/api/knowledge/reflections/{ref_v1['id']}",
        json={
            "title": "Tentativa Concorrente da Sessão A",
            "body_markdown": "Texto local da Sessão A",
            "expected_updated_at": ts_v1,
        },
        headers=headers,
    )
    assert res_a_conflict.status_code == 409
    assert "Conflito de concorrência" in res_a_conflict.json()["detail"]

    # Verifica que o banco preservou intacta a gravação da Sessão B
    current_in_db = client.get(f"/api/knowledge/reflections/{ref_v1['id']}").json()["reflection"]
    assert current_in_db["title"] == "Reflexão Editada pela Sessão B"
    assert current_in_db["body_markdown"] == "Texto salvo pela Sessão B"

    # Sessão A confirma sobrescrita (ou envia expected_updated_at atualizado ts_v2) -> 200 OK
    res_a_confirm = client.patch(
        f"/api/knowledge/reflections/{ref_v1['id']}",
        json={
            "title": "Versão Confirmada pela Sessão A",
            "body_markdown": "Texto local da Sessão A confirmado",
            "expected_updated_at": ts_v2,
        },
        headers=headers,
    )
    assert res_a_confirm.status_code == 200
    assert res_a_confirm.json()["reflection"]["title"] == "Versão Confirmada pela Sessão A"

    # 2. Testa filtro entity_type em GET /api/knowledge/links para todos os 5 tipos aprovados:
    # concept, reflection, highlight, note, book
    c1 = knowledge_service.create_concept(alice["id"], "Conceito Filtro P5.1")
    hl = annotations_service.create_highlight(
        book_id="book_p51_test",
        user_id=alice["id"],
        page=1,
        selected_text="Destaque P5.1",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        visibility="private",
    )
    nt = annotations_service.create_note(
        book_id="book_p51_test",
        user_id=alice["id"],
        page=1,
        content="Nota P5.1",
        visibility="private",
    )

    knowledge_service.create_knowledge_link(alice["id"], "concept", c1["id"], "book", "book_p51_test", "relates_to")
    knowledge_service.create_knowledge_link(alice["id"], "reflection", ref_v1["id"], "highlight", hl["id"], "references")
    knowledge_service.create_knowledge_link(alice["id"], "note", nt["id"], "concept", c1["id"], "complements")

    for etype, expected_count in (
        ("concept", 2),
        ("reflection", 1),
        ("highlight", 1),
        ("note", 1),
        ("book", 1),
    ):
        resp_filter = client.get(f"/api/knowledge/links?entity_type={etype}")
        assert resp_filter.status_code == 200, f"Falha ao filtrar conexões por entity_type={etype}"
        data_filter = resp_filter.json()
        assert data_filter["total"] == expected_count
        for item in data_filter["items"]:
            assert item["source_type"] == etype or item["target_type"] == etype

    # entity_id sem entity_type deve continuar retornando 400
    assert client.get("/api/knowledge/links?entity_id=book_p51_test").status_code == 400

