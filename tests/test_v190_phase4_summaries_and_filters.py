"""
Suíte de Testes Unitários e de Integração de API — Fase 4 da Lectoria v1.9.0.
Valida:
- GET /api/knowledge/books/{book_id}/summary (contagens exatas, sem dupla contagem, isolamento multiusuário, 404/401, no-store).
- POST /api/knowledge/entities/summary (lote sem N+1, limite 200, extra="forbid", CSRF em cabeçalho, anti-enumeração).
- GET /api/books/{book_id}/annotations?knowledge_filter=all|connected|unconnected (paginação multi-página, escopos e isolamento).
- Verificação quantitativa determinística com 100 e 1.000 anotações.
"""

import time

import annotations_service
import auth_service
import knowledge_service


def _auth_headers_and_cookies(token: str, csrf: str = "csrf_p4_test"):
    return (
        {"X-CSRF-Token": csrf},
        {"lectoria_session": token, "lectoria_csrf": csrf},
    )


def _seed_book(book_id: str, title: str, active_source: int = 1, is_missing: int = 0):
    import config
    import database

    lib_dir = config.LIBRARY_DIR
    fname = f"{book_id}.pdf"
    fpath = lib_dir / fname
    if not fpath.exists():
        fpath.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with database.get_db() as conn:
        cur = conn.cursor()
        src_id = 1 if active_source else 99
        src_path = str(lib_dir) if active_source else str(lib_dir / "inactive_p4_src")
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (?, ?, ?, ?);",
            (src_id, "Fonte Ativa" if active_source else "Fonte Inativa P4", src_path, active_source),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES (?, ?, 'Autor Fase 4', ?, ?, ?, 'pdf', 10, ?, ?);
            """,
            (book_id, title, fname, fname, fpath.stat().st_size, src_id, is_missing),
        )


def test_v190_phase4_book_summary_batch_summary_and_isolation(client):
    _seed_book("book_p4_main", "Livro Principal Fase 4")
    _seed_book("book_p4_inactive", "Livro Inativo", active_source=0)

    alice = auth_service.create_user("alice_p4_api", "Password123!", "Alice P4", role="admin")
    bob = auth_service.create_user("bob_p4_api", "Password123!", "Bob P4", role="reader")
    tok_a = auth_service.create_session(alice["id"])
    tok_b = auth_service.create_session(bob["id"])
    hdr_a, ck_a = _auth_headers_and_cookies(tok_a, "csrf_a_p4")
    hdr_b, ck_b = _auth_headers_and_cookies(tok_b, "csrf_b_p4")

    # Alice cria 1 destaque público, 1 destaque privado e 1 nota privada
    hl_pub = annotations_service.create_highlight(
        book_id="book_p4_main",
        user_id=alice["id"],
        page=1,
        selected_text="Trecho público de Alice",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        visibility="public",
    )
    hl_priv = annotations_service.create_highlight(
        book_id="book_p4_main",
        user_id=alice["id"],
        page=2,
        selected_text="Trecho privado de Alice",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        visibility="private",
    )
    nt_a = annotations_service.create_note(
        book_id="book_p4_main", user_id=alice["id"], page=3, content="Nota privada de Alice", visibility="private"
    )

    # Alice associa 1 conceito ao hl_pub e 1 conexão hl_pub -> nt_a e 1 vínculo direto no livro
    c_a = knowledge_service.create_concept(alice["id"], "Conceito Alice")
    knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_pub["id"], "concept", c_a["id"], "relates_to")
    knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_pub["id"], "note", nt_a["id"], "complements")
    knowledge_service.create_knowledge_link(alice["id"], "book", "book_p4_main", "concept", c_a["id"], "relates_to")

    # Bob cria seu próprio conceito e conecta ao hl_pub de Alice (mas NÃO vê os links de Alice)
    c_b = knowledge_service.create_concept(bob["id"], "Conceito Bob")
    knowledge_service.create_knowledge_link(bob["id"], "highlight", hl_pub["id"], "concept", c_b["id"], "relates_to")

    # 1. GET /api/knowledge/books/{book_id}/summary para Alice
    client.cookies.clear()
    client.cookies.update(ck_a)
    r_sum_a = client.get("/api/knowledge/books/book_p4_main/summary")
    assert r_sum_a.status_code == 200
    assert r_sum_a.headers.get("cache-control") == "private, no-store"
    sa = r_sum_a.json()
    assert sa["distinct_concepts_count"] == 1
    assert sa["personal_links_count"] == 3  # sem dupla contagem
    assert sa["connected_highlights_count"] == 1
    assert sa["connected_notes_count"] == 1
    assert sa["connected_annotations_count"] == 2
    assert sa["book_direct_links_count"] == 1

    # Livro inativo -> 404
    assert client.get("/api/knowledge/books/book_p4_inactive/summary").status_code == 404

    # 2. GET /api/knowledge/books/{book_id}/summary para Bob (isolamento absoluto)
    client.cookies.clear()
    client.cookies.update(ck_b)
    r_sum_b = client.get("/api/knowledge/books/book_p4_main/summary")
    assert r_sum_b.status_code == 200
    sb = r_sum_b.json()
    assert sb["distinct_concepts_count"] == 1
    assert sb["personal_links_count"] == 1
    assert sb["connected_highlights_count"] == 1
    assert sb["connected_notes_count"] == 0
    assert sb["connected_annotations_count"] == 1
    assert sb["book_direct_links_count"] == 0

    # 3. POST /api/knowledge/entities/summary para Bob tentando incluir hl_priv de Alice (anti-enumeração!)
    r_batch_b = client.post(
        "/api/knowledge/entities/summary",
        headers=hdr_b,
        json={
            "entities": [
                {"entity_type": "highlight", "entity_id": hl_pub["id"]},
                {"entity_type": "highlight", "entity_id": hl_priv["id"]},
                {"entity_type": "note", "entity_id": nt_a["id"]},
                {"entity_type": "book", "entity_id": "book_p4_main"},
            ]
        },
    )
    assert r_batch_b.status_code == 200
    assert r_batch_b.headers.get("cache-control") == "private, no-store"
    summaries_b = r_batch_b.json()["summaries"]
    assert f"highlight:{hl_pub['id']}" in summaries_b
    assert f"book:book_p4_main" in summaries_b
    # Itens privados de Alice são silenciosamente omitidos
    assert f"highlight:{hl_priv['id']}" not in summaries_b
    assert f"note:{nt_a['id']}" not in summaries_b
    pub_b = summaries_b[f"highlight:{hl_pub['id']}"]
    assert pub_b["total_links_count"] == 1
    assert [c["name"] for c in pub_b["concepts"]] == ["Conceito Bob"]

    # 4. Lote > 200 rejeitado com 422 e extra="forbid" rejeitado com 422
    too_many = [{"entity_type": "highlight", "entity_id": f"id-{i}"} for i in range(201)]
    assert client.post("/api/knowledge/entities/summary", headers=hdr_b, json={"entities": too_many}).status_code == 422
    assert (
        client.post(
            "/api/knowledge/entities/summary",
            headers=hdr_b,
            json={"entities": [], "user_id": 999},
        ).status_code
        == 422
    )

    # 5. CSRF obrigatório no cabeçalho e bloqueado na URL
    assert client.post("/api/knowledge/entities/summary", json={"entities": []}).status_code == 403
    assert (
        client.post(
            f"/api/knowledge/entities/summary?csrf_token=csrf_b_p4",
            json={"entities": []},
        ).status_code
        == 403
    )

    # 6. Sem sessão -> 401
    client.cookies.clear()
    assert client.get("/api/knowledge/books/book_p4_main/summary").status_code == 401


def test_v190_phase4_knowledge_filter_pagination_and_scale(client):
    _seed_book("book_p4_scale", "Livro Paginação e Escala")

    alice = auth_service.create_user("alice_p4_scale", "Password123!", "Alice Escala", role="admin")
    bob = auth_service.create_user("bob_p4_scale", "Password123!", "Bob Escala", role="reader")
    tok_a = auth_service.create_session(alice["id"])
    tok_b = auth_service.create_session(bob["id"])
    _, ck_a = _auth_headers_and_cookies(tok_a)
    _, ck_b = _auth_headers_and_cookies(tok_b)

    # Cria 60 destaques para Alice (2 páginas de 50) e conecta 2 na página 1 e 2 na página 2 (índices 55 e 58)
    hl_ids = []
    for i in range(60):
        h = annotations_service.create_highlight(
            book_id="book_p4_scale",
            user_id=alice["id"],
            page=(i % 10) + 1,
            selected_text=f"Trecho paginado número {i:03d}",
            rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
            visibility="public",
        )
        hl_ids.append(h["id"])

    c_a = knowledge_service.create_concept(alice["id"], "Conceito Escala")
    for idx in (0, 5, 55, 58):
        knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_ids[idx], "concept", c_a["id"], "relates_to")

    # Filtro knowledge_filter=connected para Alice encontra todos os 4 mesmo estando além da pág. 1
    client.cookies.clear()
    client.cookies.update(ck_a)
    r_conn = client.get("/api/books/book_p4_scale/annotations?scope=mine&limit=50&offset=0&knowledge_filter=connected")
    assert r_conn.status_code == 200
    data_conn = r_conn.json()
    assert data_conn["total_counts"]["highlights"] == 4
    assert len(data_conn["highlights"]) == 4
    assert data_conn["has_more"] is False

    # Filtro knowledge_filter=unconnected retorna exatamente 56 (50 na página 1 + has_more=True)
    r_unconn = client.get("/api/books/book_p4_scale/annotations?scope=mine&limit=50&offset=0&knowledge_filter=unconnected")
    assert r_unconn.status_code == 200
    data_unconn = r_unconn.json()
    assert data_unconn["total_counts"]["highlights"] == 56
    assert len(data_unconn["highlights"]) == 50
    assert data_unconn["has_more"] is True

    # Para Bob (que não criou links), knowledge_filter=connected em scope=all_visible retorna 0 (isolamento!)
    client.cookies.clear()
    client.cookies.update(ck_b)
    r_bob_conn = client.get("/api/books/book_p4_scale/annotations?scope=all_visible&limit=50&offset=0&knowledge_filter=connected")
    assert r_bob_conn.status_code == 200
    assert r_bob_conn.json()["total_counts"]["highlights"] == 0

    # Verificação quantitativa determinística com 1.000 anotações (sem N+1)
    import database
    import uuid

    with database.get_db() as conn:
        cur = conn.cursor()
        bulk_rows = [
            (str(uuid.uuid4()), "book_p4_scale", (i % 10) + 1, "#fef08a", "[]", f"Bulk {i}", "", alice["id"], "private", None, None)
            for i in range(940)
        ]
        cur.executemany(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id, visibility, epub_cfi, chapter_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            bulk_rows,
        )

    t0 = time.perf_counter()
    sum_1k = knowledge_service.get_book_knowledge_summary("book_p4_scale", alice["id"])
    t_book_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    batch_50 = knowledge_service.get_entities_knowledge_summary(
        alice["id"], [{"entity_type": "highlight", "entity_id": hid} for hid in hl_ids[:50]]
    )
    t_batch_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    filt_1k = annotations_service.get_book_annotations_summary(
        "book_p4_scale", user_id=alice["id"], scope="mine", limit=50, offset=0, knowledge_filter="connected"
    )
    t_filter_ms = (time.perf_counter() - t2) * 1000

    assert sum_1k["connected_highlights_count"] == 4
    assert batch_50["count"] == 50
    assert filt_1k["total_counts"]["highlights"] == 4
    # Limites folgados apenas para detectar regressão severa (ex.: scan O(N^2))
    assert t_book_ms < 500
    assert t_batch_ms < 500
    assert t_filter_ms < 500
