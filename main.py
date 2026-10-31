"""
Ponto de entrada da aplicação FastAPI do Lectoria.
Servidor HTTP leve para o leitor e gerenciador pessoal de livros digitais.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from database import init_db
from library import (
    BookNotFoundError,
    SecurityError,
    get_book,
    get_books,
    resolve_book_path,
    scan_library,
)
from progress import get_progress, save_progress
from collections_service import (
    CollectionNotFoundError,
    CollectionAlreadyExistsError,
    create_collection,
    list_collections,
    get_collection,
    update_collection,
    delete_collection,
    add_book_to_collection,
    remove_book_from_collection,
    set_book_collections,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Executado na inicialização e encerramento da aplicação."""
    # Garante que as tabelas SQLite existem
    init_db()
    # Executa uma varredura inicial rápida para registrar arquivos existentes
    scan_library()
    yield


app = FastAPI(
    title=config.APP_NAME,
    description="Servidor pessoal de livros digitais em PDF",
    version="1.0.0",
    lifespan=lifespan,
)

# Monta arquivos estáticos (CSS, JS, assets do PDF.js)
static_path = config.BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


class ProgressPayload(BaseModel):
    current_page: int = Field(ge=1, description="Número da página atual")
    total_pages: Optional[int] = Field(default=None, ge=1, description="Número total de páginas")


class CollectionCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Nome da coleção")
    description: Optional[str] = Field(default=None, max_length=500, description="Descrição opcional")


class CollectionBookPayload(BaseModel):
    book_id: str = Field(description="ID estável do livro")


class BookCollectionsPayload(BaseModel):
    collection_ids: list[int] = Field(description="Lista de IDs de coleções")


# --- Rotas da Interface do Usuário ---

@app.api_route("/", methods=["GET", "HEAD"], response_class=FileResponse)
async def serve_library_page():
    """Entrega a página principal da biblioteca."""
    return FileResponse(static_path / "index.html")


@app.api_route("/reader/{book_id}", methods=["GET", "HEAD"], response_class=FileResponse)
async def serve_reader_page(book_id: str):
    """Valida o livro e entrega a página do leitor."""
    try:
        get_book(book_id)
    except BookNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livro não encontrado ou arquivo indisponível.",
        )
    return FileResponse(static_path / "reader.html")


# --- Endpoints da API REST ---

@app.get("/api/health")
async def health_check():
    """Verificação de integridade e configurações ativas."""
    return {
        "status": "healthy",
        "app_name": config.APP_NAME,
        "library_dir": str(config.LIBRARY_DIR),
        "data_dir": str(config.DATA_DIR),
    }


@app.get("/api/books")
async def list_books(
    search: Optional[str] = None,
    sort_by: str = "title",
    order: str = "asc",
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
):
    """Lista todos os livros disponíveis com filtros, coleções e ordenação."""
    books = get_books(
        search=search,
        sort_by=sort_by,
        order=order,
        status_filter=status,
        collection_id=collection_id,
    )
    return {"books": books, "count": len(books)}


# --- Endpoints de Coleções e Categorias ---

@app.get("/api/collections")
async def get_collections():
    """Lista todas as coleções cadastradas."""
    return {"collections": list_collections()}


@app.post("/api/collections", status_code=status.HTTP_201_CREATED)
async def create_new_collection(payload: CollectionCreatePayload):
    """Cria uma nova coleção de livros."""
    try:
        created = create_collection(name=payload.name, description=payload.description)
        return {"status": "success", "collection": created}
    except CollectionAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/collections/{collection_id}")
async def retrieve_collection(collection_id: int):
    """Retorna detalhes de uma coleção específica."""
    try:
        return get_collection(collection_id)
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/collections/{collection_id}")
async def modify_collection(collection_id: int, payload: CollectionCreatePayload):
    """Atualiza o nome e descrição de uma coleção."""
    try:
        updated = update_collection(
            collection_id=collection_id,
            name=payload.name,
            description=payload.description,
        )
        return {"status": "success", "collection": updated}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CollectionAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/collections/{collection_id}")
async def remove_collection(collection_id: int):
    """Remove uma coleção (sem apagar os arquivos físicos dos PDFs)."""
    try:
        delete_collection(collection_id)
        return {"status": "success", "message": "Coleção excluída com sucesso."}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/collections/{collection_id}/books")
async def add_book_to_col(collection_id: int, payload: CollectionBookPayload):
    """Adiciona um livro a uma coleção."""
    try:
        add_book_to_collection(collection_id, payload.book_id)
        return {"status": "success", "message": "Livro adicionado à coleção."}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.delete("/api/collections/{collection_id}/books/{book_id}")
async def remove_book_from_col(collection_id: int, book_id: str):
    """Remove a associação de um livro com uma coleção."""
    removed = remove_book_from_collection(collection_id, book_id)
    return {"status": "success", "removed": removed}


@app.put("/api/books/{book_id}/collections")
async def update_book_collections_endpoint(book_id: str, payload: BookCollectionsPayload):
    """Atualiza a lista completa de coleções às quais o livro pertence."""
    try:
        cols = set_book_collections(book_id, payload.collection_ids)
        return {"status": "success", "collections": cols}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/books/{book_id}")
async def retrieve_book(book_id: str):
    """Retorna detalhes de um livro específico."""
    try:
        return get_book(book_id)
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/scan")
async def trigger_scan():
    """
    Executa a varredura da biblioteca sob demanda.
    Identifica novos arquivos, atualizações e itens removidos.
    """
    stats = scan_library()
    return {"status": "success", "stats": stats}


@app.get("/api/books/{book_id}/file")
async def stream_book_file(book_id: str):
    """
    Disponibiliza o arquivo PDF com suporte a HTTP Range requests (206 Partial Content).
    Permite ao PDF.js no cliente requisitar apenas as partes necessárias do documento
    sem que o servidor carregue o arquivo inteiro na memória.
    """
    try:
        file_path = resolve_book_path(book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=file_path.name,
    )


@app.get("/api/books/{book_id}/progress")
async def read_progress(book_id: str):
    """Retorna o progresso de leitura registrado para o livro."""
    try:
        get_book(book_id)
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    prog = get_progress(book_id)
    if not prog:
        return {"current_page": 1, "total_pages": 1, "percentage": 0.0}
    return prog


@app.post("/api/books/{book_id}/progress")
async def update_progress(book_id: str, request: Request):
    """
    Atualiza o progresso de leitura.
    Aceita payloads JSON tradicionais ou form-data/beacon ao fechar a janela.
    """
    try:
        book = get_book(book_id)
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Suporta JSON e dados enviados via navigator.sendBeacon
    current_page = 1
    total_pages = book.get("page_count") or 1

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        current_page = int(body.get("current_page", 1))
        if body.get("total_pages"):
            total_pages = int(body["total_pages"])
    else:
        # beacon ou text/plain contendo json string
        raw_body = await request.body()
        if raw_body:
            import json
            try:
                data = json.loads(raw_body.decode("utf-8"))
                current_page = int(data.get("current_page", 1))
                if data.get("total_pages"):
                    total_pages = int(data["total_pages"])
            except Exception:
                pass

    updated = save_progress(
        book_id=book_id,
        current_page=current_page,
        total_pages=total_pages,
    )
    return {"status": "success", "progress": updated}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  # Estritamente 1 worker para baixo consumo de memória
        reload=False,
    )
