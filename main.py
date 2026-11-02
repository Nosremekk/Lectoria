"""
Ponto de entrada da aplicação FastAPI do Lectoria.
Servidor HTTP leve para o leitor e gerenciador pessoal de livros digitais.
Suporta streaming com HTTP Range, downloads, múltiplas fontes e perfis com isolamento de dados.
"""

from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

import config
from database import init_db
from auth_service import (
    SESSION_COOKIE_NAME,
    AuthError,
    RateLimitError,
    UserAlreadyExistsError,
    UserNotFoundError,
    authenticate_user,
    create_session,
    create_user,
    delete_user,
    destroy_session,
    get_user_from_session,
    has_admin_user,
    list_users,
    update_user,
)
from sources_service import (
    InvalidSourceError,
    SourceError,
    SourceNotFoundError,
    add_library_source,
    delete_library_source,
    get_library_source,
    list_library_sources,
    set_source_active,
)
from directory_picker_service import (
    NativePickerUnavailableError,
    RemoteNativePickerForbiddenError,
    browse_server_directories,
    pick_directory_native,
)
from covers_service import (
    CoverError,
    CoverTooLargeError,
    CoverValidationError,
    delete_custom_cover,
    extract_uploaded_image_from_request,
    get_or_create_book_cover,
    save_custom_cover,
    save_rendered_cover_cache,
)
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
from annotations_service import (
    AnnotationNotFoundError,
    create_highlight,
    get_highlights,
    delete_highlight,
    create_note,
    get_notes,
    update_note,
    delete_note,
    get_page_drawing,
    save_page_drawing,
    delete_page_drawing,
    get_book_annotations_summary,
)
from backup_service import (
    BackupError,
    create_backup,
    list_backups,
    restore_backup,
)
from export_service import (
    ImportError,
    export_book_annotations_markdown,
    export_book_annotations_json,
    preview_import_annotations,
    apply_import_annotations,
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
    version="1.1.1",
    lifespan=lifespan,
)

CSRF_COOKIE_NAME = "lectoria_csrf"
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def csrf_and_security_middleware(request: Request, call_next):
    """
    Middleware de segurança:
    1. Valida correspondência de Host com Origin/Referer em requisições mutantes para barrar CSRF cross-origin.
    2. Aplica validação Double Submit Cookie CSRF caso o cookie lectoria_csrf esteja presente.
    3. Emite cookie lectoria_csrf transparente para clientes em navegação (GET/HEAD).
    """
    # 1. Proteção de Origem e CSRF para métodos que modificam estado
    if request.method not in SAFE_HTTP_METHODS:
        host = request.headers.get("host")
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        if origin:
            parsed_origin = urlparse(origin)
            if parsed_origin.netloc and host and parsed_origin.netloc.lower() != host.lower():
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Origem da requisição não permitida (CSRF detectado)."},
                )
        elif referer:
            parsed_referer = urlparse(referer)
            if parsed_referer.netloc and host and parsed_referer.netloc.lower() != host.lower():
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Referer da requisição não permitido (CSRF detectado)."},
                )

        # 2. Validação Double Submit Cookie CSRF
        # Se o cliente possui o cookie lectoria_csrf, valida o token enviado no cabeçalho ou parâmetro
        expected_csrf = request.cookies.get(CSRF_COOKIE_NAME)
        if expected_csrf:
            provided_csrf = (
                request.headers.get("x-csrf-token")
                or request.query_params.get("csrf_token")
            )
            if not provided_csrf or not secrets.compare_digest(provided_csrf, expected_csrf):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Token CSRF inválido ou ausente."},
                )

    response = await call_next(request)

    # 3. Emissão transparente do cookie CSRF em navegação de páginas HTML (fora de /api/)
    is_html_page = (
        request.method in SAFE_HTTP_METHODS
        and not request.url.path.startswith("/api/")
    )
    if is_html_page and CSRF_COOKIE_NAME not in request.cookies:
        new_csrf = secrets.token_urlsafe(32)
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=new_csrf,
            httponly=False,  # Legível pelo JavaScript para inclusão no cabeçalho X-CSRF-Token
            samesite="lax",
            path="/",
        )

    return response

# Monta arquivos estáticos (CSS, JS, assets do PDF.js)
static_path = config.BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# =========================================================================
# MODELOS DE DADOS (PYDANTIC SCHEMAS)
# =========================================================================

class LoginPayload(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SetupPayload(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=4)
    display_name: Optional[str] = None


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=4)
    display_name: Optional[str] = None
    role: str = Field(default="reader")


class UserUpdatePayload(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None


class SourceCreatePayload(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_name_or_label(self):
        if not self.name and not self.label:
            raise ValueError("O nome ou rótulo da fonte é obrigatório.")
        if not self.name:
            self.name = self.label
        return self


class SourceActivePayload(BaseModel):
    is_active: bool


class NativePickerPayload(BaseModel):
    initial_dir: Optional[str] = None


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


class HighlightCreatePayload(BaseModel):
    page: int = Field(ge=1, description="Número da página")
    color: str = Field(default="#fef08a", description="Cor de destaque em hexadecimal")
    rects: list[dict] = Field(description="Retângulos normalizados de seleção")
    selected_text: str = Field(min_length=1, description="Texto selecionado")
    text_context: Optional[str] = Field(default=None, description="Contexto do texto")


class NoteCreatePayload(BaseModel):
    page: int = Field(ge=1, description="Número da página")
    content: str = Field(min_length=1, description="Conteúdo da anotação")
    color: Optional[str] = Field(default="#fef08a", description="Cor temática")
    highlight_id: Optional[str] = Field(default=None, description="ID do destaque vinculado se houver")


class NoteUpdatePayload(BaseModel):
    content: str = Field(min_length=1, description="Conteúdo da anotação")
    color: Optional[str] = Field(default=None, description="Cor temática")


class DrawingSavePayload(BaseModel):
    strokes: list[dict] = Field(description="Array de traços vetoriais com pontos normalizados")


# =========================================================================
# CONTROLE DE SESSÃO E AUTORIZAÇÃO
# =========================================================================

def get_current_user(request: Request) -> Optional[dict]:
    """Obtém o usuário associado à sessão atual via Cookie ou Bearer Token."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

    if token:
        user = get_user_from_session(token)
        if user:
            return user

    # Compatibilidade suave quando nenhum administrador foi configurado ainda
    if not has_admin_user():
        return {"id": 1, "username": "default", "display_name": "Leitor", "role": "admin", "is_active": 1}

    return None


def require_user(request: Request) -> dict:
    """Garante que a requisição é realizada por um usuário autenticado e ativo."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária para acessar este recurso.",
        )
    return user


def require_admin(request: Request) -> dict:
    """Garante que a requisição é realizada por um usuário com papel de administrador."""
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores.",
        )
    return user


# =========================================================================
# ROTAS DA INTERFACE DO USUÁRIO
# =========================================================================

@app.api_route("/", methods=["GET", "HEAD"], response_class=FileResponse)
async def serve_library_page():
    """Entrega a página principal da biblioteca."""
    return FileResponse(static_path / "index.html")


@app.api_route("/reader/{book_id}", methods=["GET", "HEAD"], response_class=FileResponse)
async def serve_reader_page(book_id: str, request: Request):
    """Valida o livro e entrega a página do leitor."""
    user = get_current_user(request)
    user_id = user["id"] if user else 1
    try:
        get_book(book_id, user_id=user_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livro não encontrado ou arquivo indisponível.",
        )
    return FileResponse(static_path / "reader.html")


# =========================================================================
# ENDPOINTS DE AUTENTICAÇÃO E PERFIS DE USUÁRIO
# =========================================================================

@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Verifica se há sessão ativa e se o sistema já possui administrador cadastrado."""
    admin_exists = has_admin_user()
    user = get_current_user(request)
    if not admin_exists:
        return {
            "authenticated": True,
            "has_admin": False,
            "user": user,
            "setup_required": True,
        }

    # Se há admin cadastrado, verifica se o usuário autenticado realmente tem sessão
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers.get("authorization", "")[7:].strip()

    real_user = get_user_from_session(token) if token else None
    return {
        "authenticated": real_user is not None,
        "has_admin": True,
        "user": real_user,
        "setup_required": False,
    }


@app.post("/api/auth/setup")
async def setup_initial_admin(payload: SetupPayload, response: Response):
    """Configura o primeiro administrador do sistema quando nenhum existir."""
    if has_admin_user():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="O administrador inicial já foi configurado anteriormente.",
        )

    try:
        user = create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name or payload.username.capitalize(),
            role="admin",
        )
        token = create_session(user["id"])
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=7 * 24 * 3600,
        )
        return {"status": "success", "user": user, "token": token}
    except (ValueError, UserAlreadyExistsError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/auth/login")
async def login(payload: LoginPayload, request: Request, response: Response):
    """Autentica o usuário e inicializa cookie de sessão HTTP seguro."""
    client_ip = request.client.host if request.client else ""
    try:
        user = authenticate_user(payload.username, payload.password, client_ip=client_ip)
        token = create_session(user["id"])
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=7 * 24 * 3600,
        )
        return {"status": "success", "user": user, "token": token}
    except RateLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Encerra a sessão ativa e limpa os cookies."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers.get("authorization", "")[7:].strip()

    if token:
        destroy_session(token)

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "success", "message": "Sessão finalizada com sucesso."}


@app.get("/api/auth/me")
async def get_me(request: Request):
    """Retorna os dados do usuário autenticado."""
    user = require_user(request)
    return {"user": user}


@app.get("/api/auth/csrf")
async def get_csrf_token_endpoint(request: Request, response: Response):
    """Retorna o token CSRF atual ou gera um novo caso não exista."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            httponly=False,
            samesite="lax",
            path="/",
        )
    return {"csrf_token": token}


# =========================================================================
# ENDPOINTS ADMINISTRATIVOS: GERENCIAMENTO DE USUÁRIOS
# =========================================================================

@app.get("/api/users")
async def list_users_endpoint(request: Request):
    """Lista todos os usuários (administrador apenas)."""
    require_admin(request)
    return {"users": list_users()}


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(payload: UserCreatePayload, request: Request):
    """Cria um novo usuário leitor ou administrador."""
    require_admin(request)
    try:
        created = create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            role=payload.role,
        )
        return {"status": "success", "user": created}
    except (ValueError, UserAlreadyExistsError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/users/{user_id}")
async def update_user_endpoint(user_id: int, payload: UserUpdatePayload, request: Request):
    """Atualiza dados, papel, status ou senha de um usuário."""
    require_admin(request)
    try:
        updated = update_user(
            user_id=user_id,
            display_name=payload.display_name,
            role=payload.role,
            is_active=payload.is_active,
            new_password=payload.new_password,
        )
        return {"status": "success", "user": updated}
    except UserNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ValueError, AuthError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, request: Request):
    """Remove um usuário do sistema."""
    require_admin(request)
    try:
        delete_user(user_id)
        return {"status": "success", "message": "Usuário excluído com sucesso."}
    except UserNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =========================================================================
# ENDPOINTS ADMINISTRATIVOS: FONTES DE BIBLIOTECA (DIRETÓRIOS MÚLTIPLOS)
# =========================================================================

@app.get("/api/sources")
async def list_sources_endpoint(request: Request):
    """Lista todas as fontes cadastradas (administrador apenas)."""
    require_admin(request)
    return {"sources": list_library_sources()}


@app.get("/api/sources/browse")
async def browse_sources_directories_endpoint(request: Request, path: Optional[str] = None):
    """
    Navegador visual seguro de diretórios do servidor (administrador apenas).
    Permite explorar pastas autorizadas sem digitar caminhos manualmente.
    """
    require_admin(request)
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else None)
    try:
        return browse_server_directories(raw_path=path, client_host=client_host)
    except InvalidSourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/sources/pick-native")
async def pick_native_directory_endpoint(
    request: Request,
    payload: Optional[NativePickerPayload] = None,
):
    """
    Aciona o seletor nativo de diretórios do sistema operacional no servidor local.
    Bloqueado para acessos remotos ou ambientes sem sessão gráfica.
    """
    require_admin(request)
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else None)
    initial_dir = payload.initial_dir if payload else None
    try:
        return pick_directory_native(client_host=client_host, initial_dir=initial_dir)
    except RemoteNativePickerForbiddenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except NativePickerUnavailableError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except InvalidSourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/sources", status_code=status.HTTP_201_CREATED)
async def add_source_endpoint(payload: SourceCreatePayload, request: Request):
    """Cadastra um novo diretório como fonte de livros."""
    require_admin(request)
    try:
        source = add_library_source(payload.name, payload.path)
        # Executa uma varredura inicial da nova fonte adicionada
        scan_library(source["id"])
        return {"status": "success", "source": get_library_source(source["id"])}
    except (ValueError, SourceError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/sources/{source_id}/active")
async def toggle_source_active_endpoint(source_id: int, payload: SourceActivePayload, request: Request):
    """Ativa ou desativa uma fonte."""
    require_admin(request)
    try:
        updated = set_source_active(source_id, payload.is_active)
        return {"status": "success", "source": updated}
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.delete("/api/sources/{source_id}")
async def remove_source_endpoint(source_id: int, request: Request):
    """Remove uma fonte de biblioteca (nunca apaga os PDFs físicos)."""
    require_admin(request)
    try:
        delete_library_source(source_id)
        return {"status": "success", "message": "Fonte removida com sucesso."}
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/sources/{source_id}/scan")
async def scan_single_source_endpoint(source_id: int, request: Request):
    """Executa a varredura manual de uma fonte específica."""
    require_admin(request)
    try:
        get_library_source(source_id)
        stats = scan_library(source_id)
        return {"status": "success", "stats": stats, "source": get_library_source(source_id)}
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# ENDPOINTS DO ACERVO DE LIVROS E DOWNLOAD
# =========================================================================

@app.get("/api/health")
async def health_check():
    """Verificação de integridade do servidor sem exposição de caminhos internos."""
    return {
        "status": "healthy",
        "app_name": config.APP_NAME,
        "version": "1.1.1",
    }


@app.get("/api/books")
async def list_books(
    request: Request,
    search: Optional[str] = None,
    sort_by: str = "title",
    order: str = "asc",
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
):
    """Lista todos os livros disponíveis com filtros, coleções e ordenação para o usuário autenticado."""
    user = require_user(request)
    books = get_books(
        search=search,
        sort_by=sort_by,
        order=order,
        status_filter=status,
        collection_id=collection_id,
        user_id=user["id"],
    )
    return {"books": books, "count": len(books)}


@app.get("/api/books/{book_id}")
async def retrieve_book(book_id: str, request: Request):
    """Retorna detalhes de um livro específico para o usuário autenticado."""
    user = require_user(request)
    try:
        return get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/books/{book_id}/file")
async def stream_book_file(book_id: str, request: Request):
    """
    Disponibiliza o arquivo PDF com suporte a HTTP Range requests (206 Partial Content).
    Permite ao PDF.js no cliente requisitar apenas as partes necessárias do documento
    sem que o servidor carregue o arquivo inteiro na memória.
    """
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
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


@app.get("/api/books/{book_id}/download")
async def download_book_file(book_id: str, request: Request):
    """
    Entrega o arquivo original do PDF para download direto no dispositivo do leitor.
    Utiliza streaming de arquivo sem carregar o PDF na memória.
    """
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
        file_path = resolve_book_path(book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Sanitiza o nome do arquivo para o cabeçalho Content-Disposition
    raw_name = book.get("title") or file_path.stem
    clean_title = "".join(c for c in raw_name if c.isalnum() or c in (" ", "-", "_", ".")).strip()
    if not clean_title.lower().endswith(".pdf"):
        clean_title += ".pdf"

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=clean_title,
        content_disposition_type="attachment",
    )


@app.get("/api/books/{book_id}/cover")
async def get_book_cover_endpoint(book_id: str, request: Request):
    """
    Entrega a capa do livro (capa personalizada, capa extraída em cache ou capa editorial SVG).
    """
    require_user(request)
    try:
        cover = get_or_create_book_cover(book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
        "X-Cover-Type": cover["type"],
    }
    if cover["file_path"] is not None:
        return FileResponse(
            path=cover["file_path"],
            media_type=cover["media_type"],
            headers=headers,
        )

    headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(
        content=cover["content"],
        media_type=cover["media_type"],
        headers=headers,
    )


@app.post("/api/books/{book_id}/cover")
async def upload_custom_book_cover_endpoint(book_id: str, request: Request):
    """
    Define ou substitui uma capa personalizada (PNG, JPEG ou WebP) para um livro.
    """
    require_user(request)
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        image_bytes = extract_uploaded_image_from_request(body, content_type)
        result = save_custom_cover(book_id, image_bytes)
        return {"status": "success", **result}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CoverTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except (CoverValidationError, CoverError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/cover")
async def remove_custom_book_cover_endpoint(book_id: str, request: Request):
    """
    Remove a capa personalizada de um livro e restaura a capa automática (extraída ou editorial).
    """
    require_user(request)
    try:
        result = delete_custom_cover(book_id)
        return {"status": "success", **result}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/books/{book_id}/cover/cache")
async def cache_rendered_book_cover_endpoint(book_id: str, request: Request):
    """
    Armazena em cache a miniatura da primeira página renderizada pelo PDF.js no cliente,
    quando o PDF não possui imagem embutida direta e ainda não tem capa personalizada.
    """
    require_user(request)
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        image_bytes = extract_uploaded_image_from_request(body, content_type)
        result = save_rendered_cover_cache(book_id, image_bytes)
        return {"status": "success", **result}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CoverTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except (CoverValidationError, CoverError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/scan")
async def trigger_scan(request: Request):
    """
    Executa a varredura manual de todas as fontes sob demanda.
    Requer privilégios de administrador.
    """
    require_admin(request)
    stats = scan_library()
    return {"status": "success", "stats": stats}


# =========================================================================
# ENDPOINTS DE COLEÇÕES E CATEGORIAS (ISOLADOS POR USUÁRIO)
# =========================================================================

@app.get("/api/collections")
async def get_collections(request: Request):
    """Lista todas as coleções do usuário autenticado."""
    user = require_user(request)
    return {"collections": list_collections(user_id=user["id"])}


@app.post("/api/collections", status_code=status.HTTP_201_CREATED)
async def create_new_collection(payload: CollectionCreatePayload, request: Request):
    """Cria uma nova coleção vinculada ao usuário autenticado."""
    user = require_user(request)
    try:
        created = create_collection(name=payload.name, description=payload.description, user_id=user["id"])
        return {"status": "success", "collection": created}
    except CollectionAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/collections/{collection_id}")
async def retrieve_collection(collection_id: int, request: Request):
    """Retorna detalhes de uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        return get_collection(collection_id, user_id=user["id"])
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/collections/{collection_id}")
async def modify_collection(collection_id: int, payload: CollectionCreatePayload, request: Request):
    """Atualiza o nome e descrição de uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        updated = update_collection(
            collection_id=collection_id,
            name=payload.name,
            description=payload.description,
            user_id=user["id"],
        )
        return {"status": "success", "collection": updated}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CollectionAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/collections/{collection_id}")
async def remove_collection(collection_id: int, request: Request):
    """Remove uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        delete_collection(collection_id, user_id=user["id"])
        return {"status": "success", "message": "Coleção excluída com sucesso."}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/collections/{collection_id}/books")
async def add_book_to_col(collection_id: int, payload: CollectionBookPayload, request: Request):
    """Adiciona um livro a uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        add_book_to_collection(collection_id, payload.book_id, user_id=user["id"])
        return {"status": "success", "message": "Livro adicionado à coleção."}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.delete("/api/collections/{collection_id}/books/{book_id}")
async def remove_book_from_col(collection_id: int, book_id: str, request: Request):
    """Remove a associação de um livro com uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        removed = remove_book_from_collection(collection_id, book_id, user_id=user["id"])
        return {"status": "success", "removed": removed}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/books/{book_id}/collections")
async def update_book_collections_endpoint(book_id: str, payload: BookCollectionsPayload, request: Request):
    """Atualiza as coleções do usuário às quais o livro pertence."""
    user = require_user(request)
    try:
        cols = set_book_collections(book_id, payload.collection_ids, user_id=user["id"])
        return {"status": "success", "collections": cols}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# ENDPOINTS DE ANOTAÇÕES, MARCA-TEXTO E DESENHOS (ISOLADOS POR USUÁRIO)
# =========================================================================

@app.get("/api/books/{book_id}/annotations")
async def get_all_book_annotations(book_id: str, request: Request):
    """Retorna as anotações, destaques e desenhos do usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    summary = get_book_annotations_summary(book_id, user_id=user["id"])
    return summary


@app.get("/api/books/{book_id}/pages/{page}/annotations")
async def get_page_annotations(book_id: str, page: int, request: Request):
    """Retorna os destaques, notas e desenhos de uma página para o usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    highlights = get_highlights(book_id, page=page, user_id=user["id"])
    notes = get_notes(book_id, page=page, user_id=user["id"])
    drawing = get_page_drawing(book_id, page, user_id=user["id"])

    return {
        "page": page,
        "highlights": highlights,
        "notes": notes,
        "drawing": drawing,
    }


@app.post("/api/books/{book_id}/highlights", status_code=status.HTTP_201_CREATED)
async def create_new_highlight(book_id: str, payload: HighlightCreatePayload, request: Request):
    """Cria um novo destaque de texto vinculado ao usuário autenticado."""
    user = require_user(request)
    try:
        highlight = create_highlight(
            book_id=book_id,
            page=payload.page,
            color=payload.color,
            rects=payload.rects,
            selected_text=payload.selected_text,
            text_context=payload.text_context,
            user_id=user["id"],
        )
        return {"status": "success", "highlight": highlight}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/highlights/{highlight_id}")
@app.delete("/api/highlights/{highlight_id}")
async def remove_highlight(highlight_id: str, request: Request, book_id: Optional[str] = None):
    """Remove um destaque do usuário autenticado."""
    user = require_user(request)
    deleted = delete_highlight(highlight_id, user_id=user["id"])
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destaque não encontrado.")
    return {"status": "success", "message": "Destaque removido com sucesso."}


@app.post("/api/books/{book_id}/notes", status_code=status.HTTP_201_CREATED)
async def create_new_note(book_id: str, payload: NoteCreatePayload, request: Request):
    """Cria uma nova nota textual vinculada ao usuário autenticado."""
    user = require_user(request)
    try:
        note = create_note(
            book_id=book_id,
            page=payload.page,
            content=payload.content,
            color=payload.color,
            highlight_id=payload.highlight_id,
            user_id=user["id"],
        )
        return {"status": "success", "note": note}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/books/{book_id}/notes/{note_id}")
@app.put("/api/notes/{note_id}")
async def modify_note(note_id: str, payload: NoteUpdatePayload, request: Request, book_id: Optional[str] = None):
    """Atualiza uma nota do usuário autenticado."""
    user = require_user(request)
    try:
        updated = update_note(
            note_id=note_id,
            content=payload.content,
            color=payload.color,
            user_id=user["id"],
        )
        return {"status": "success", "note": updated}
    except AnnotationNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/notes/{note_id}")
@app.delete("/api/notes/{note_id}")
async def remove_note(note_id: str, request: Request, book_id: Optional[str] = None):
    """Exclui uma nota do usuário autenticado."""
    user = require_user(request)
    deleted = delete_note(note_id, user_id=user["id"])
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nota não encontrada.")
    return {"status": "success", "message": "Nota removida com sucesso."}


@app.get("/api/books/{book_id}/pages/{page}/drawing")
async def get_drawing_endpoint(book_id: str, page: int, request: Request):
    """Recupera os traços vetoriais da página para o usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    drawing = get_page_drawing(book_id=book_id, page=page, user_id=user["id"])
    return drawing


@app.put("/api/books/{book_id}/pages/{page}/drawing")
async def update_page_drawing(book_id: str, page: int, payload: DrawingSavePayload, request: Request):
    """Salva os traços vetoriais da página para o usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    try:
        result = save_page_drawing(book_id=book_id, page=page, strokes=payload.strokes, user_id=user["id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/pages/{page}/drawing")
async def clear_page_drawing(book_id: str, page: int, request: Request):
    """Limpa todos os traços de desenho da página para o usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    deleted = delete_page_drawing(book_id=book_id, page=page, user_id=user["id"])
    return {"status": "success", "cleared": deleted}


# =========================================================================
# ENDPOINTS DE PROGRESSO DE LEITURA
# =========================================================================

@app.get("/api/books/{book_id}/progress")
async def read_progress(book_id: str, request: Request):
    """Retorna o progresso de leitura registrado para o usuário autenticado."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    prog = get_progress(book_id, user_id=user["id"])
    if not prog:
        return {"current_page": 1, "total_pages": 1, "percentage": 0.0}
    return prog


@app.post("/api/books/{book_id}/progress")
async def update_progress(book_id: str, request: Request):
    """Atualiza o progresso de leitura do usuário autenticado."""
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    current_page = 1
    total_pages = book.get("page_count") or 1

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        current_page = int(body.get("current_page", 1))
        if body.get("total_pages"):
            total_pages = int(body["total_pages"])
    else:
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
        user_id=user["id"],
    )
    return {"status": "success", "progress": updated}


# =========================================================================
# EXPORTAÇÃO E IMPORTAÇÃO DE ESTUDOS
# =========================================================================

@app.get("/api/books/{book_id}/export/markdown")
async def export_annotations_markdown(book_id: str, request: Request):
    """Exporta todas as anotações do usuário autenticado formatadas em Markdown."""
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
        md_content = export_book_annotations_markdown(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    safe_title = "".join(c for c in book["title"] if c.isalnum() or c in ("-", "_")).strip() or "estudo"
    filename = f"estudo_{safe_title}.md"
    return Response(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/books/{book_id}/export/json")
async def export_annotations_json(book_id: str, request: Request):
    """Exporta todas as anotações do usuário autenticado estruturadas em JSON."""
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
        json_data = export_book_annotations_json(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    safe_title = "".join(c for c in book["title"] if c.isalnum() or c in ("-", "_")).strip() or "estudo"
    filename = f"estudo_{safe_title}.json"
    import json
    return Response(
        content=json.dumps(json_data, indent=2, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


MAX_IMPORT_BODY_BYTES = 15 * 1024 * 1024  # 15 MB


async def _parse_import_body(request: Request) -> dict:
    """Valida o tamanho do corpo da requisição e decodifica o JSON de importação."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_IMPORT_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Arquivo de importação excede o tamanho máximo permitido de 15 MB.",
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > MAX_IMPORT_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Arquivo de importação excede o tamanho máximo permitido de 15 MB.",
        )

    import json
    try:
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O payload de importação deve ser um objeto JSON.",
            )
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"JSON corrompido ou malformado: {e}",
        )


@app.post("/api/books/{book_id}/import/preview")
async def preview_annotations_import(book_id: str, request: Request):
    """Valida o JSON de anotações e retorna um resumo antes da confirmação para o usuário."""
    user = require_user(request)
    try:
        payload = await _parse_import_body(request)
        preview = preview_import_annotations(book_id, payload, user_id=user["id"])
        return {"status": "success", "preview": preview}
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/books/{book_id}/import/confirm")
async def confirm_annotations_import(book_id: str, request: Request):
    """Aplica a importação de anotações dentro de uma transação SQLite protegida para o usuário."""
    user = require_user(request)
    try:
        payload = await _parse_import_body(request)
        result = apply_import_annotations(book_id, payload, user_id=user["id"])
        return {"status": "success", "result": result}
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =========================================================================
# BACKUPS MANUAIS DO BANCO SQLITE (ADMINISTRADOR APENAS)
# =========================================================================

@app.post("/api/backup")
async def trigger_manual_backup(request: Request):
    """Gera uma cópia de segurança pontual do banco SQLite em backups/."""
    require_admin(request)
    try:
        backup_info = create_backup()
        return {"status": "success", "backup": backup_info}
    except BackupError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/api/backups")
async def get_available_backups(request: Request):
    """Lista todos os backups manuais disponíveis no servidor."""
    require_admin(request)
    backups = list_backups()
    return {"status": "success", "backups": backups}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  # Estritamente 1 worker para baixo consumo de memória
        reload=False,
    )
