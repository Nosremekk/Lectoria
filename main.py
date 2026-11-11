"""
Ponto de entrada da aplicação FastAPI do Lectoria.
Servidor HTTP leve para o leitor e gerenciador pessoal de livros digitais.
Suporta streaming com HTTP Range, downloads, múltiplas fontes e perfis com isolamento de dados.
"""

from contextlib import asynccontextmanager
from email.utils import formatdate
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

import config
from database import SCHEMA_VERSION, get_db, init_db
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
    list_admin_audit_logs,
    list_users,
    log_admin_action,
    revoke_user_sessions,
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
    validate_cover_image,
)
from library import (
    BookNotFoundError,
    SecurityError,
    get_book,
    get_books,
    get_discovery_shelves,
    get_library_diagnostics,
    set_book_reading_status,
    update_book_metadata,
    resolve_book_path,
    resolve_book_file_for_user,
    scan_library,
)
from taxonomy_service import (
    batch_update_book_categories,
    create_category,
    delete_category,
    get_book_taxonomy,
    get_taxonomy_tree,
    get_uncategorized_books_count,
    list_categories,
    merge_categories,
    reclassify_all_auto_books,
    reorder_categories,
    set_book_categories,
    update_category,
)
from background_enricher import enricher
from metadata_service import (
    CrossrefClient,
    OpenLibraryClient,
    GoogleBooksClient,
    get_providers_diagnostics,
    score_candidate,
    score_candidate_detailed,
    identify_book_metadata,
)
import automation_service
import social_service
from social_service import SocialNotFoundError, SocialPermissionError
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
    verify_sqlite_integrity,
)
from export_service import (
    ImportError,
    export_book_annotations_markdown,
    export_book_annotations_json,
    preview_import_annotations,
    apply_import_annotations,
)
import clubs_service
from clubs_service import (
    ClubError,
    ClubNotFoundError,
    ClubPermissionError,
    CollectiveReadNotFoundError,
    InviteNotFoundError,
    InvalidOperationError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Executado na inicialização e encerramento da aplicação."""
    # Garante que as tabelas SQLite existem e migrações estão aplicadas
    init_db()
    # Recupera tarefas em segundo plano que foram interrompidas por reinício anterior
    automation_service.recover_interrupted_jobs_on_startup()
    # Executa uma varredura inicial rápida para registrar arquivos existentes
    scan_library()
    # Inicia loop leve do agendador e monitor de diretórios
    automation_service.scheduler_loop.start()
    try:
        yield
    finally:
        automation_service.scheduler_loop.stop()


app = FastAPI(
    title=config.APP_NAME,
    description="Servidor pessoal de livros digitais em PDF",
    version=config.APP_VERSION,
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

    # 3. Cabeçalhos fundamentais de proteção contra MIME sniffing, clickjacking e vazamento de referer
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    # 4. Emissão transparente do cookie CSRF em navegação de páginas HTML (fora de /api/)
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


class MetadataSearchPayload(BaseModel):
    query: Optional[str] = None
    isbn: Optional[str] = None
    provider: Optional[str] = None


class MetadataApplyPayload(BaseModel):
    candidate_id: int
    apply_cover: bool = True
    lock_fields: Optional[List[str]] = None


class MetadataUpdatePayload(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    subtitle: Optional[str] = None
    volume: Optional[str] = None
    isbn10: Optional[str] = None
    isbn13: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None
    description: Optional[str] = None
    original_title: Optional[str] = None
    work_id: Optional[str] = None
    edition: Optional[str] = None
    identification_status: Optional[str] = None
    taxonomy: Optional[Dict[str, Any]] = None
    lock_fields: Optional[List[str]] = None


class MetadataQueueEnqueuePayload(BaseModel):
    book_ids: Optional[List[str]] = None
    only_pending: bool = True


class MetadataReviewPayload(BaseModel):
    status: str = "manual_override"


class BatchMetadataUpdatePayload(BaseModel):
    book_ids: List[Union[str, int]]
    publisher: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    primary_category: Optional[str] = None
    identification_status: Optional[str] = None


class ScheduleUpdatePayload(BaseModel):
    schedule_mode: Optional[str] = None
    interval_minutes: Optional[int] = None
    run_time: Optional[str] = None
    days_of_week: Optional[List[int]] = None
    timezone: Optional[str] = None
    is_enabled: Optional[bool] = None
    enabled: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class JobRunPayload(BaseModel):
    job_type: str
    run_async: bool = False


class ProfileUpdatePayload(BaseModel):
    display_name: Optional[str] = None
    avatar_color: Optional[str] = None
    bio: Optional[str] = None
    reading_prefs: Optional[Dict[str, Any]] = None


class PrivacyUpdatePayload(BaseModel):
    share_current_reading: Optional[bool] = None
    share_reading_history: Optional[bool] = None
    share_reviews: Optional[bool] = None
    share_lists: Optional[bool] = None
    share_annotations_default: Optional[bool] = None


class BookReviewPayload(BaseModel):
    rating: Optional[int] = None
    review_text: Optional[str] = None
    is_favorite: Optional[bool] = None
    want_to_read: Optional[bool] = None
    is_public: Optional[bool] = None


class SharedAnnotationCreatePayload(BaseModel):
    book_id: Union[str, int]
    quote_text: Optional[str] = None
    comment_text: Optional[str] = None
    content: Optional[str] = None
    page_number: Optional[int] = None
    chapter_name: Optional[str] = None
    progress_locator: Optional[str] = None
    color: str = "yellow"
    visibility: str = "public"
    note_id: Optional[Union[str, int]] = None
    source_note_id: Optional[Union[str, int]] = None


class SharedAnnotationUpdatePayload(BaseModel):
    comment_text: Optional[str] = None
    content: Optional[str] = None
    visibility: Optional[str] = None


class AnnotationCommentPayload(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_id: Optional[int] = None


class RecommendationCreatePayload(BaseModel):
    book_id: Union[str, int]
    to_user_id: Optional[int] = None
    recipient_user_id: Optional[int] = None
    message: Optional[str] = None


class ClearCachePayload(BaseModel):
    clear_api_cache: bool = True
    clear_extracted_covers: bool = False
    clear_auto_covers: bool = False


# =========================================================================
# MODELOS DE DADOS PARA CLUBES E LEITURA COLETIVA (v1.6.0)
# =========================================================================

class ClubCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    visibility: str = "public"


class ClubUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None


class ClubArchivePayload(BaseModel):
    archive: bool = True


class OwnershipTransferPayload(BaseModel):
    new_owner_id: int


class MemberRoleUpdatePayload(BaseModel):
    role: str


class ClubInvitePayload(BaseModel):
    invitee_id: int


class InviteResponsePayload(BaseModel):
    accept: bool = True


class CollectiveReadCreatePayload(BaseModel):
    book_id: str
    title: Optional[str] = None
    description: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "planned"


class CollectiveReadUpdatePayload(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None


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
    return FileResponse(
        static_path / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


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


@app.get("/reader.html")
async def serve_reader_page_html_alias(request: Request, id: Optional[str] = None):
    """Redireciona links com parâmetro ?id=... para o caminho oficial /reader/{id}."""
    if id:
        return RedirectResponse(url=f"/reader/{id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
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
            "version": config.APP_VERSION,
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
        "version": config.APP_VERSION,
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
        "version": config.APP_VERSION,
    }


@app.get("/api/books")
async def list_books(
    request: Request,
    search: Optional[str] = None,
    sort_by: str = "title",
    order: str = "asc",
    status: Optional[str] = None,
    collection_id: Optional[int] = None,
    source_id: Optional[int] = None,
    format: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    genre: Optional[str] = None,
    origin: Optional[str] = None,
    language: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    identification_status: Optional[str] = None,
    fts_query: Optional[str] = None,
    favorite_only: bool = False,
    want_to_read_only: bool = False,
    uncategorized: bool = False,
    collective_read_only: bool = False,
):
    """Lista todos os livros disponíveis com filtros multidimensionais, coleções e ordenação."""
    user = require_user(request)
    books = get_books(
        search=search,
        sort_by=sort_by,
        order=order,
        status_filter=status,
        collection_id=collection_id,
        source_id=source_id,
        format_filter=format,
        category_filter=category,
        subcategory_filter=subcategory,
        genre_filter=genre,
        origin_filter=origin,
        language_filter=language,
        year_from=year_from,
        year_to=year_to,
        identification_status=identification_status,
        fts_query=fts_query,
        favorite_only=favorite_only,
        want_to_read_only=want_to_read_only,
        uncategorized=uncategorized,
        collective_read_only=collective_read_only,
        user_id=user["id"],
    )
    return {"books": books, "count": len(books)}


@app.get("/api/discovery/shelves")
async def discovery_shelves_endpoint(request: Request):
    """Retorna as prateleiras dinâmicas de descoberta inspiradas em serviços de streaming."""
    user = require_user(request)
    shelves = get_discovery_shelves(user_id=user["id"])
    return {"shelves": shelves, "count": len(shelves)}


@app.get("/api/taxonomy/tree")
async def taxonomy_tree_endpoint(request: Request):
    """Retorna a árvore taxonômica estruturada de categorias presentes no acervo."""
    require_user(request)
    tree = get_taxonomy_tree()
    return {"tree": tree}


@app.get("/api/categories")
async def list_categories_endpoint(request: Request, include_empty: bool = True):
    """Lista as categorias gerenciadas da biblioteca com ordenação e contagem."""
    require_user(request)
    cats = list_categories(include_empty=include_empty)
    uncat_cnt = get_uncategorized_books_count()
    return {"categories": cats, "uncategorized_count": uncat_cnt}


class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    display_order: Optional[int] = None


@app.post("/api/categories")
async def create_category_endpoint(payload: CategoryCreateRequest, request: Request):
    """Cria uma nova categoria (acesso exclusivo admin)."""
    admin = require_admin(request)
    try:
        cat = create_category(
            name=payload.name,
            description=payload.description,
            display_order=payload.display_order,
            is_custom=True,
        )
        log_admin_action(
            admin_user_id=admin["id"],
            action="create_category",
            target_type="category",
            target_id=str(cat["id"]),
            details={"name": cat["name"]},
        )
        return cat
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class CategoryUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    display_order: Optional[int] = None


@app.put("/api/categories/{category_id}")
async def update_category_endpoint(category_id: int, payload: CategoryUpdateRequest, request: Request):
    """Atualiza dados e ordenação de uma categoria (acesso admin)."""
    admin = require_admin(request)
    try:
        cat = update_category(
            category_id=category_id,
            name=payload.name,
            description=payload.description,
            display_order=payload.display_order,
        )
        log_admin_action(
            admin_user_id=admin["id"],
            action="update_category",
            target_type="category",
            target_id=str(category_id),
            details={"name": cat["name"], "display_order": cat["display_order"]},
        )
        return cat
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/categories/{category_id}")
async def delete_category_endpoint(category_id: int, request: Request):
    """Exclui uma categoria sem nunca apagar livros (acesso admin)."""
    admin = require_admin(request)
    try:
        res = delete_category(category_id)
        log_admin_action(
            admin_user_id=admin["id"],
            action="delete_category",
            target_type="category",
            target_id=str(category_id),
            details=res,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class CategoryMergeRequest(BaseModel):
    source_category_id: int
    target_category_id: int


@app.post("/api/categories/merge")
async def merge_categories_endpoint(payload: CategoryMergeRequest, request: Request):
    """Mescla duas categorias transferindo todas as obras (acesso admin)."""
    admin = require_admin(request)
    try:
        res = merge_categories(payload.source_category_id, payload.target_category_id)
        log_admin_action(
            admin_user_id=admin["id"],
            action="merge_categories",
            target_type="category",
            target_id=str(payload.target_category_id),
            details=res,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class CategoryReorderRequest(BaseModel):
    ordered_ids: List[int]


@app.post("/api/categories/reorder")
async def reorder_categories_endpoint(payload: CategoryReorderRequest, request: Request):
    """Reordena a exibição de categorias (acesso admin)."""
    admin = require_admin(request)
    cats = reorder_categories(payload.ordered_ids)
    log_admin_action(
        admin_user_id=admin["id"],
        action="reorder_categories",
        target_type="category",
        details={"ordered_ids": payload.ordered_ids},
    )
    return {"categories": cats}


class BookCategoriesSetRequest(BaseModel):
    categories: List[str]


@app.put("/api/books/{book_id}/categories")
async def set_book_categories_endpoint(book_id: str, payload: BookCategoriesSetRequest, request: Request):
    """Atribui categorias manuais a um livro (acesso leitor/admin autenticado)."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    res = set_book_categories(book_id, payload.categories, is_manual=True)
    return res


class BatchCategoryAssignmentRequest(BaseModel):
    book_ids: List[str]
    add_categories: Optional[List[str]] = None
    remove_categories: Optional[List[str]] = None
    replace_categories: Optional[List[str]] = None


@app.post("/api/books/batch-categories")
async def batch_categories_endpoint(payload: BatchCategoryAssignmentRequest, request: Request):
    """Atribui, adiciona ou remove categorias em múltiplos livros de uma vez."""
    require_admin(request)
    try:
        res = batch_update_book_categories(
            book_ids=payload.book_ids,
            add_categories=payload.add_categories,
            remove_categories=payload.remove_categories,
            replace_categories=payload.replace_categories,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


class ReadingStatusSetRequest(BaseModel):
    status: str = Field(..., pattern="^(unread|in_progress|completed)$")


@app.put("/api/books/{book_id}/reading-status")
async def set_reading_status_endpoint(book_id: str, payload: ReadingStatusSetRequest, request: Request):
    """Atualiza o status de leitura do livro para o usuário atual (unread, in_progress, completed)."""
    user = require_user(request)
    try:
        book = set_book_reading_status(book_id, user_id=user["id"], status=payload.status)
        return {"status": payload.status, "book": book}
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/taxonomy/reclassify-auto")
async def reclassify_auto_endpoint(request: Request):
    """Reavalia classificações automáticas sem mexer em categorizações manuais (acesso admin)."""
    admin = require_admin(request)
    res = reclassify_all_auto_books()
    log_admin_action(
        admin_user_id=admin["id"],
        action="reclassify_auto_books",
        target_type="taxonomy",
        details=res,
    )
    return res


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
@app.head("/api/books/{book_id}/file")
async def stream_book_file(book_id: str, request: Request):
    """
    Disponibiliza o arquivo PDF/EPUB com suporte a HTTP Range requests (206 Partial Content),
    inspeção de cabeçalhos (HEAD), validação condicional com ETag e políticas seguras de cache.
    Utiliza consulta otimizada de caminho sem carregar tabelas acessórias da biblioteca.
    """
    user = require_user(request)
    try:
        file_path, stat_result, media_type = resolve_book_file_for_user(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    etag_base = f"{stat_result.st_mtime}-{stat_result.st_size}"
    etag = f'"{hashlib.md5(etag_base.encode(), usedforsecurity=False).hexdigest()}"'
    last_modified = formatdate(stat_result.st_mtime, usegmt=True)

    cache_headers = {
        "Cache-Control": "private, no-cache",
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }

    # Validação condicional If-None-Match (para requisições completas ou revalidações sem Range)
    if_none_match = request.headers.get("if-none-match")
    if not request.headers.get("range") and if_none_match and if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
        stat_result=stat_result,
        headers=cache_headers,
    )


@app.get("/api/books/{book_id}/download")
async def download_book_file(book_id: str, request: Request):
    """
    Entrega o arquivo original do PDF ou EPUB para download direto no dispositivo do leitor.
    Utiliza streaming de arquivo sem carregar o documento na memória.
    """
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
        file_path = resolve_book_path(book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    ext = file_path.suffix.lower()
    is_epub = ext == ".epub"
    media_type = "application/epub+zip" if is_epub else "application/pdf"
    default_ext = ".epub" if is_epub else ".pdf"

    # Sanitiza o nome do arquivo para o cabeçalho Content-Disposition
    raw_name = book.get("title") or file_path.stem
    clean_title = "".join(c for c in raw_name if c.isalnum() or c in (" ", "-", "_", ".")).strip()
    if not clean_title.lower().endswith(default_ext):
        clean_title += default_ext

    return FileResponse(
        path=file_path,
        media_type=media_type,
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
# ENDPOINTS DE METADADOS BIBLIOGRÁFICOS E RECONCILIAÇÃO EXTERNA (v1.4.0)
# =========================================================================

@app.get("/api/books/{book_id}/metadata/candidates")
async def get_book_metadata_candidates_endpoint(book_id: str, request: Request):
    """Retorna os candidatos a metadados externos registrados para o livro."""
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, source as provider, external_id, title, author, publisher,
                   publish_year, isbn10, isbn13, cover_url,
                   score as confidence_score, confidence_level as match_status,
                   raw_json as raw_data, created_at
            FROM metadata_candidates
            WHERE book_id = ?
            ORDER BY score DESC, id DESC
            """,
            (book_id,),
        )
        candidates = [dict(r) for r in cursor.fetchall()]

    return {"book_id": book_id, "candidates": candidates, "count": len(candidates)}


@app.post("/api/books/{book_id}/metadata/search")
async def search_book_metadata_endpoint(book_id: str, payload: MetadataSearchPayload, request: Request):
    """
    Realiza busca externa manual (Open Library e/ou Google Books) para encontrar
    candidatos bibliográficos precisos para o livro.
    """
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    q_title = payload.query or book.get("title")
    q_isbn = payload.isbn or book.get("isbn13") or book.get("isbn10")
    q_author = book.get("author")

    raw_candidates: List[Dict[str, Any]] = []

    # 1. Consulta Open Library
    if payload.provider in (None, "", "open_library", "all"):
        if q_isbn:
            ol_isbn_res = OpenLibraryClient.lookup_isbn(q_isbn)
            if ol_isbn_res:
                raw_candidates.append(ol_isbn_res)
        if q_title:
            ol_res = OpenLibraryClient.search(q_title, author=q_author, limit=5)
            raw_candidates.extend(ol_res)

    # 2. Consulta Google Books
    if payload.provider in (None, "", "google_books", "all"):
        if q_isbn:
            gb_isbn_res = GoogleBooksClient.lookup_isbn(q_isbn)
            if gb_isbn_res:
                raw_candidates.append(gb_isbn_res)
        if q_title:
            gb_res = GoogleBooksClient.search(q_title, author=q_author, limit=5)
            raw_candidates.extend(gb_res)

    # 3. Consulta Crossref
    if payload.provider in (None, "", "crossref", "all"):
        if q_isbn:
            cr_isbn_res = CrossrefClient.lookup_isbn(q_isbn)
            if cr_isbn_res:
                raw_candidates.append(cr_isbn_res)
        if q_title:
            cr_res = CrossrefClient.search(q_title, author=q_author, limit=5)
            raw_candidates.extend(cr_res)

    scored_candidates = []
    with get_db() as conn:
        cursor = conn.cursor()
        for cand in raw_candidates:
            score, match_status, reasons = score_candidate_detailed(book, cand)
            cand["confidence_score"] = score
            cand["match_status"] = match_status
            cand["score_reasons"] = reasons

            cursor.execute(
                """
                INSERT INTO metadata_candidates (
                    book_id, source, external_id, title, author, publisher,
                    publish_year, isbn10, isbn13, cover_url, raw_json,
                    score, confidence_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    cand.get("source") or cand.get("provider", "unknown"),
                    cand.get("external_id") or "ext_1",
                    cand.get("title") or "",
                    cand.get("author"),
                    cand.get("publisher"),
                    cand.get("publish_year"),
                    cand.get("isbn10"),
                    cand.get("isbn13"),
                    cand.get("cover_url"),
                    json.dumps(cand),
                    score,
                    match_status,
                ),
            )
            cand["id"] = cursor.lastrowid
            scored_candidates.append(cand)

    scored_candidates.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)
    return {"book_id": book_id, "candidates": scored_candidates, "count": len(scored_candidates)}


@app.post("/api/books/{book_id}/metadata/apply")
async def apply_book_metadata_candidate_endpoint(book_id: str, payload: MetadataApplyPayload, request: Request):
    """
    Aplica formalmente os metadados de um candidato aprovado ao livro, com opção
    de travar campos específicos e aplicar a capa oficial.
    """
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM metadata_candidates WHERE id = ? AND book_id = ?",
            (payload.candidate_id, book_id),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidato não encontrado para esta obra.")
        candidate = dict(row)

    raw_info = {}
    if candidate.get("raw_json"):
        try:
            raw_info = json.loads(candidate["raw_json"])
        except Exception:
            pass

    cand_desc = raw_info.get("description")
    cand_subtitle = raw_info.get("subtitle")
    cand_provider = candidate.get("source") or raw_info.get("provider", "external")

    updates: Dict[str, Any] = {
        "title": candidate["title"] or book["title"],
        "subtitle": cand_subtitle or book.get("subtitle"),
        "author": candidate["author"] or book["author"],
        "isbn10": candidate["isbn10"] or book.get("isbn10"),
        "isbn13": candidate["isbn13"] or book.get("isbn13"),
        "publisher": candidate["publisher"] or book.get("publisher"),
        "publish_year": candidate["publish_year"] or book.get("publish_year"),
        "description": cand_desc or book.get("description"),
        "identification_status": "identified",
        "metadata_source": cand_provider,
        "reviewed_by": user["id"],
        "last_error_reason": None,
    }
    if payload.lock_fields:
        updates["lock_fields"] = payload.lock_fields

    update_book_metadata(book_id, updates, user_id=user["id"])

    # Se solicitado, tenta baixar e validar a capa externa
    if payload.apply_cover and candidate.get("cover_url") and book.get("cover_type") != "custom":
        enricher._download_external_cover_safely(book_id, candidate["cover_url"])

    return {"status": "success", "book": get_book(book_id, user_id=user["id"])}


@app.put("/api/books/{book_id}/metadata")
async def update_book_metadata_endpoint(book_id: str, payload: MetadataUpdatePayload, request: Request):
    """Atualização direta de metadados pelo usuário com sincronização FTS5 e travas."""
    user = require_user(request)
    try:
        book = get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    updates = payload.model_dump(exclude_unset=True)
    updated = update_book_metadata(book_id, updates, user_id=user["id"])
    return {"status": "success", "book": updated}


# =========================================================================
# ENDPOINTS DA FILA DE ENRIQUECIMENTO EM SEGUNDO PLANO (ADMIN)
# =========================================================================

@app.get("/api/metadata/queue/status")
async def metadata_queue_status_endpoint(request: Request):
    """Consulta o status detalhado da fila de enriquecimento em segundo plano."""
    require_admin(request)
    return enricher.get_status()


@app.post("/api/metadata/queue/start")
async def metadata_queue_start_endpoint(request: Request):
    """Inicia ou retoma o processamento assíncrono da fila de enriquecimento."""
    require_admin(request)
    enricher.start()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/pause")
async def metadata_queue_pause_endpoint(request: Request):
    """Pausa a fila de enriquecimento sem perder a posição atual."""
    require_admin(request)
    enricher.pause()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/cancel")
async def metadata_queue_cancel_endpoint(request: Request):
    """Cancela e esvazia a fila de enriquecimento."""
    require_admin(request)
    enricher.cancel()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/enqueue")
async def metadata_queue_enqueue_endpoint(payload: MetadataQueueEnqueuePayload, request: Request):
    """Enfileira obras para enriquecimento inteligente."""
    require_admin(request)
    count = enricher.enqueue_books(payload.book_ids, only_pending=payload.only_pending)
    enricher.start()
    return {"status": "success", "enqueued_count": count, "queue": enricher.get_status()}


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
    progress_locator = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        current_page = int(body.get("current_page", 1))
        if body.get("total_pages"):
            total_pages = int(body["total_pages"])
        progress_locator = body.get("progress_locator")
    else:
        raw_body = await request.body()
        if raw_body:
            import json
            try:
                data = json.loads(raw_body.decode("utf-8"))
                current_page = int(data.get("current_page", 1))
                if data.get("total_pages"):
                    total_pages = int(data["total_pages"])
                progress_locator = data.get("progress_locator")
            except Exception:
                pass

    updated = save_progress(
        book_id=book_id,
        current_page=current_page,
        total_pages=total_pages,
        user_id=user["id"],
        progress_locator=progress_locator,
    )
    social_service.record_reading_milestone(
        user_id=user["id"],
        book_id=book_id,
        percentage=updated.get("percentage", 0.0),
        current_page=current_page,
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


# =========================================================================
# ENDPOINTS DE REVISÃO BIBLIOGRÁFICA E DIAGNÓSTICO DE METADADOS (v1.5.0)
# =========================================================================

@app.get("/api/metadata/diagnostics")
async def metadata_diagnostics_endpoint(request: Request):
    """
    Retorna métricas detalhadas por provedor (Open Library, Google Books, Crossref),
    contagem de obras por status de identificação e lista de obras pendentes de revisão.
    """
    require_admin(request)
    providers = get_providers_diagnostics()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(identification_status, 'pending') as st, COUNT(*) as cnt
            FROM books
            WHERE is_missing = 0
            GROUP BY st;
            """
        )
        status_counts = {r["st"]: r["cnt"] for r in cursor.fetchall()}

        cursor.execute(
            """
            SELECT id, title, author, subtitle, volume, edition, filename, rel_path,
                   isbn10, isbn13, publisher, publish_year, language,
                   identification_status, identification_score, metadata_source,
                   last_error_reason, reviewed_at
            FROM books
            WHERE is_missing = 0
              AND COALESCE(identification_status, 'pending') IN ('needs_review', 'ambiguous', 'not_found', 'rate_limited', 'technical_error', 'pending')
            ORDER BY
              CASE COALESCE(identification_status, 'pending')
                WHEN 'ambiguous' THEN 1
                WHEN 'needs_review' THEN 2
                WHEN 'rate_limited' THEN 3
                WHEN 'technical_error' THEN 4
                WHEN 'not_found' THEN 5
                ELSE 6
              END,
              identification_score DESC,
              title COLLATE NOCASE ASC
            LIMIT 100;
            """
        )
        review_books = [dict(r) for r in cursor.fetchall()]

        if review_books:
            r_ids = [b["id"] for b in review_books]
            placeholders = ",".join("?" for _ in r_ids)
            cursor.execute(
                f"""
                SELECT id, book_id, source, external_id, title, author, publisher,
                       publish_year, isbn10, isbn13, cover_url, score, confidence_level, raw_json
                FROM metadata_candidates
                WHERE book_id IN ({placeholders})
                ORDER BY score DESC;
                """,
                r_ids,
            )
            cand_map: Dict[str, List[Dict[str, Any]]] = {}
            for cr in cursor.fetchall():
                cd = dict(cr)
                try:
                    raw = json.loads(cd.get("raw_json") or "{}")
                    cd["score_reasons"] = raw.get("_score_reasons", [])
                except Exception:
                    cd["score_reasons"] = []
                cand_map.setdefault(cd["book_id"], []).append(cd)
            for b in review_books:
                b["candidates"] = cand_map.get(b["id"], [])[:5]

    return {
        "providers": providers,
        "status_counts": status_counts,
        "queue": enricher.get_status(),
        "review_books": review_books,
    }


@app.post("/api/books/{book_id}/metadata/review")
async def mark_book_metadata_reviewed_endpoint(
    book_id: str,
    request: Request,
    payload: Optional[MetadataReviewPayload] = None,
):
    """Marca um livro como revisado manualmente pelo administrador/usuário."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    target_status = (payload.status if payload and payload.status else "manual_override")
    with get_db() as conn:
        conn.execute(
            """
            UPDATE books
            SET identification_status = ?,
                reviewed_at = CURRENT_TIMESTAMP,
                reviewed_by = ?,
                last_error_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (target_status, user["id"], book_id),
        )
    return {"status": "success", "book": get_book(book_id, user_id=user["id"])}


@app.post("/api/books/{book_id}/metadata/reject-candidates")
async def reject_book_metadata_candidates_endpoint(book_id: str, request: Request):
    """Rejeita os candidatos automáticos de um livro e marca como revisado (manual_override)."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    with get_db() as conn:
        conn.execute("DELETE FROM metadata_candidates WHERE book_id = ?;", (book_id,))
        conn.execute(
            """
            UPDATE books
            SET identification_status = 'manual_override',
                reviewed_at = CURRENT_TIMESTAMP,
                reviewed_by = ?,
                last_error_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (user["id"], book_id),
        )
    return {"status": "success", "book": get_book(book_id, user_id=user["id"])}


@app.post("/api/books/{book_id}/metadata/reprocess")
async def reprocess_single_book_metadata_endpoint(book_id: str, request: Request):
    """Reprocessa imediatamente a identificação bibliográfica de um livro específico."""
    user = require_admin(request)
    try:
        get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    enricher._enrich_single_book_sync(book_id)
    return {"status": "success", "book": get_book(book_id, user_id=user["id"])}


@app.post("/api/metadata/batch-update")
async def batch_update_metadata_endpoint(payload: BatchMetadataUpdatePayload, request: Request):
    """Edição em lote de categoria, editora, idioma ou status para múltiplos livros (Admin)."""
    admin = require_admin(request)
    if not payload.book_ids:
        raise HTTPException(status_code=400, detail="Nenhum livro selecionado.")

    updated_count = 0
    chosen_cat = payload.category or payload.primary_category
    for raw_bid in payload.book_ids[:200]:
        bid = str(raw_bid).strip()
        try:
            updates: Dict[str, Any] = {}
            if payload.publisher is not None and payload.publisher.strip():
                updates["publisher"] = payload.publisher.strip()
            if payload.language is not None and payload.language.strip():
                updates["language"] = payload.language.strip()
            if payload.identification_status is not None and payload.identification_status.strip():
                updates["identification_status"] = payload.identification_status.strip()
            if chosen_cat is not None and chosen_cat.strip():
                updates["taxonomy"] = {
                    "categories": [chosen_cat.strip()],
                    "subcategories": [],
                    "genre": None,
                    "origin": None,
                    "period": None,
                }
            if updates:
                update_book_metadata(bid, updates, user_id=admin["id"])
                updated_count += 1
        except Exception:
            pass

    log_admin_action(admin["id"], "batch_update_metadata", f"{updated_count} livros", f"Campos: {payload.model_dump(exclude_none=True)}")
    return {"status": "success", "updated_count": updated_count}


# =========================================================================
# ENDPOINTS DE AUTOMAÇÃO E FILA PERSISTENTE DE TAREFAS (Seção 4)
# =========================================================================

@app.get("/api/automation/schedules")
async def list_automation_schedules_endpoint(request: Request):
    """Lista todas as rotinas agendadas configuráveis (Admin)."""
    require_admin(request)
    return {"schedules": automation_service.list_schedules()}


@app.put("/api/automation/schedules/{job_type}")
async def update_automation_schedule_endpoint(
    job_type: str,
    payload: ScheduleUpdatePayload,
    request: Request,
):
    """Atualiza o modo de agendamento, horário, dias da semana ou intervalo de uma rotina (Admin)."""
    admin = require_admin(request)
    try:
        updated = automation_service.update_schedule(job_type, payload.model_dump(exclude_none=True))
        log_admin_action(admin["id"], "update_schedule", job_type, json.dumps(payload.model_dump(exclude_none=True)))
        return {"status": "success", "schedule": updated}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/automation/jobs")
async def list_automation_jobs_endpoint(
    request: Request,
    limit: int = 50,
    status: Optional[str] = None,
):
    """Lista o histórico persistente de execuções de tarefas em segundo plano (Admin)."""
    require_admin(request)
    return {"jobs": automation_service.list_jobs(limit=limit, status_filter=status)}


@app.post("/api/automation/jobs/run")
async def run_automation_job_endpoint(payload: JobRunPayload, request: Request):
    """Dispara imediatamente uma tarefa da fila persistente (Admin)."""
    admin = require_admin(request)
    try:
        job = automation_service.run_job(
            job_type=payload.job_type,
            trigger_source="manual",
            run_async=payload.run_async,
        )
        log_admin_action(admin["id"], "run_job", payload.job_type, f"Job ID #{job.get('id')}")
        return {"status": "success", "job": job}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/automation/jobs/{job_id}/cancel")
async def cancel_automation_job_endpoint(job_id: int, request: Request):
    """Cancela com segurança uma tarefa pendente ou em andamento (Admin)."""
    admin = require_admin(request)
    try:
        job = automation_service.cancel_job(job_id)
        log_admin_action(admin["id"], "cancel_job", f"job #{job_id}", "")
        return {"status": "success", "job": job}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# ENDPOINTS DO PAINEL ADMINISTRATIVO DEDICADO (Seção 6)
# =========================================================================

@app.get("/api/admin/dashboard")
async def admin_dashboard_endpoint(request: Request):
    """Retorna todas as métricas operacionais consolidadas para o Dashboard Administrativo."""
    require_admin(request)

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0;")
        total_books = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0 AND format = 'pdf';")
        pdf_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0 AND format = 'epub';")
        epub_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 1;")
        missing_count = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COALESCE(identification_status, 'pending') as st, COUNT(*) as cnt
            FROM books WHERE is_missing = 0
            GROUP BY st;
            """
        )
        id_stats = {r["st"]: r["cnt"] for r in cursor.fetchall()}

        cursor.execute(
            """
            SELECT COALESCE(cover_type, 'none') as ct, COUNT(*) as cnt
            FROM books WHERE is_missing = 0
            GROUP BY ct;
            """
        )
        cover_stats = {r["ct"]: r["cnt"] for r in cursor.fetchall()}

        cursor.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_active, 1) = 1;")
        active_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > CURRENT_TIMESTAMP;")
        active_sessions = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM shared_annotations WHERE visibility IN ('public', 'users');")
        shared_notes_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_reviews WHERE rating IS NOT NULL;")
        reviews_count = cursor.fetchone()[0]

    db_size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
    covers_size = 0
    if config.COVERS_DIR.exists():
        for f in config.COVERS_DIR.glob("*"):
            if f.is_file():
                try:
                    covers_size += f.stat().st_size
                except OSError:
                    pass

    backups_size = 0
    if config.BACKUPS_DIR.exists():
        for f in config.BACKUPS_DIR.rglob("*"):
            if f.is_file():
                try:
                    backups_size += f.stat().st_size
                except OSError:
                    pass

    try:
        disk_usage = shutil.disk_usage(str(config.DATA_DIR))
        free_disk = disk_usage.free
        total_disk = disk_usage.total
    except Exception:
        free_disk = 0
        total_disk = 0

    prov_list = get_providers_diagnostics()
    return {
        "version": config.APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "library": {
            "total_books": total_books,
            "pdf_count": pdf_count,
            "pdf_books": pdf_count,
            "epub_count": epub_count,
            "epub_books": epub_count,
            "missing_count": missing_count,
            "identification": id_stats,
            "by_identification_status": id_stats,
            "covers": cover_stats,
        },
        "covers": {
            "custom": cover_stats.get("custom", 0),
            "extracted": cover_stats.get("extracted", 0),
            "editorial_fallback": cover_stats.get("none", 0),
        },
        "users": {
            "total_users": active_users,
            "active_users": active_users,
            "active_sessions": active_sessions,
            "shared_annotations": shared_notes_count,
            "reviews_count": reviews_count,
        },
        "storage": {
            "db_size_bytes": db_size,
            "db_bytes": db_size,
            "covers_size_bytes": covers_size,
            "backups_size_bytes": backups_size,
            "free_disk_bytes": free_disk,
            "disk_free_bytes": free_disk,
            "total_disk_bytes": total_disk,
        },
        "providers": {"providers": prov_list},
        "recent_jobs": automation_service.list_jobs(limit=10),
        "schedules": automation_service.list_schedules(),
    }


@app.get("/api/admin/library/diagnostics")
async def admin_library_diagnostics_endpoint(request: Request):
    """Retorna diagnósticos detalhados de arquivos ausentes, duplicatas, metadados incompletos e formatos não reconhecidos."""
    require_admin(request)
    return get_library_diagnostics()


@app.post("/api/users/{user_id}/revoke-sessions")
async def revoke_user_sessions_endpoint(user_id: int, request: Request):
    """Encerra todas as sessões ativas de um usuário específico (Admin)."""
    admin = require_admin(request)
    count = revoke_user_sessions(user_id)
    log_admin_action(admin["id"], "revoke_user_sessions", f"user_id={user_id}", f"{count} sessões encerradas")
    return {"status": "success", "revoked_sessions": count}


@app.get("/api/admin/audit-logs")
async def admin_audit_logs_endpoint(request: Request, limit: int = 100):
    """Retorna os logs de auditoria administrativa."""
    require_admin(request)
    return {"logs": list_admin_audit_logs(limit=limit)}


@app.get("/api/admin/maintenance/status")
async def admin_maintenance_status_endpoint(request: Request):
    """Retorna status detalhado do banco SQLite, cache, disco e integridade para a aba Manutenção."""
    require_admin(request)
    integrity_ok = verify_sqlite_integrity(config.DB_PATH)
    with get_db() as conn:
        api_cache_count = conn.execute("SELECT COUNT(*) FROM metadata_api_cache;").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM books_fts;").fetchone()[0]

    extracted_covers = list(config.COVERS_DIR.glob("*_extracted.*")) if config.COVERS_DIR.exists() else []
    custom_covers = list(config.COVERS_DIR.glob("*_custom.*")) if config.COVERS_DIR.exists() else []

    try:
        disk = shutil.disk_usage(str(config.DATA_DIR))
        free_bytes = disk.free
        total_bytes = disk.total
    except Exception:
        free_bytes = 0
        total_bytes = 0

    return {
        "version": config.APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sqlite_integrity": "ok" if integrity_ok else "error",
        "db_integrity": "ok" if integrity_ok else "error",
        "db_path": str(config.DB_PATH),
        "db_size_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
        "api_cache_entries": api_cache_count,
        "external_cache_entries": api_cache_count,
        "fts_indexed_books": fts_count,
        "extracted_covers_count": len(extracted_covers),
        "custom_covers_count": len(custom_covers),
        "covers_files_count": len(extracted_covers) + len(custom_covers),
        "free_disk_bytes": free_bytes,
        "disk_free_bytes": free_bytes,
        "total_disk_bytes": total_bytes,
        "backups": list_backups(),
        "providers": get_providers_diagnostics(),
    }


@app.post("/api/admin/maintenance/clear-cache")
async def admin_clear_cache_endpoint(payload: ClearCachePayload, request: Request):
    """Limpa o cache de consultas externas e/ou o cache de capas extraídas (preservando capas customizadas)."""
    admin = require_admin(request)
    cleared_api = 0
    cleared_covers = 0

    with get_db() as conn:
        if payload.clear_api_cache:
            cur = conn.execute("DELETE FROM metadata_api_cache;")
            cleared_api = cur.rowcount
        if payload.clear_extracted_covers or payload.clear_auto_covers:
            if config.COVERS_DIR.exists():
                for f in config.COVERS_DIR.glob("*_extracted.*"):
                    try:
                        f.unlink()
                        cleared_covers += 1
                    except OSError:
                        pass
            conn.execute(
                """
                UPDATE books
                SET cover_type = 'none', cover_path = NULL, cover_source_hash = NULL
                WHERE cover_type = 'extracted';
                """
            )

    log_admin_action(
        admin["id"],
        "clear_cache",
        "system_cache",
        f"api_cache={cleared_api}, extracted_covers={cleared_covers}",
    )
    return {
        "status": "success",
        "cleared_api_cache": cleared_api,
        "cleared_extracted_covers": cleared_covers,
    }


# =========================================================================
# ENDPOINTS MULTIUSUÁRIO, PRIVACIDADE E "NOSSA BIBLIOTECA" (Seção 5)
# =========================================================================

@app.get("/api/profile/me")
async def get_my_profile_endpoint(request: Request):
    """Retorna o perfil completo, estatísticas e configurações de privacidade do usuário logado."""
    user = require_user(request)
    prof = social_service.get_user_profile(user["id"], viewer_user_id=user["id"])
    return {"profile": prof, **prof}


@app.put("/api/profile/me")
async def update_my_profile_endpoint(payload: ProfileUpdatePayload, request: Request):
    """Atualiza nome de exibição, cor de avatar, bio e preferências de leitura do usuário logado."""
    user = require_user(request)
    updated = social_service.update_user_profile(user["id"], payload.model_dump(exclude_none=True))
    return {"status": "success", "profile": updated, **updated}


@app.get("/api/profile/privacy")
@app.get("/api/profile/me/privacy")
async def get_my_privacy_endpoint(request: Request):
    """Retorna as configurações de privacidade do usuário logado (privado por padrão)."""
    user = require_user(request)
    priv = social_service.get_privacy_settings(user["id"])
    return {"privacy": priv, **priv}


@app.put("/api/profile/privacy")
@app.put("/api/profile/me/privacy")
async def update_my_privacy_endpoint(payload: PrivacyUpdatePayload, request: Request):
    """Atualiza as permissões de compartilhamento do usuário logado."""
    user = require_user(request)
    updated = social_service.update_privacy_settings(user["id"], payload.model_dump(exclude_none=True))
    return {"status": "success", "privacy": updated, **updated}


@app.get("/api/users/{target_user_id}/profile")
async def get_other_user_profile_endpoint(target_user_id: int, request: Request):
    """Retorna o perfil de outro usuário filtrando estritamente pelos controles de privacidade dele."""
    user = require_user(request)
    try:
        prof = social_service.get_user_profile(target_user_id, viewer_user_id=user["id"])
        return {"profile": prof, **prof}
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/social/users")
async def list_social_users_endpoint(request: Request):
    """Lista os membros ativos da biblioteca para seleção em recomendações comunitárias."""
    user = require_user(request)
    feed = social_service.get_nossa_biblioteca_feed(viewer_user_id=user["id"])
    return {"users": feed.get("members", []), "current_user_id": user["id"]}


@app.get("/api/books/{book_id}/reviews")
async def get_book_reviews_endpoint(book_id: str, request: Request):
    """Retorna avaliações (1 a 5 estrelas) e resenhas compartilhadas de um livro."""
    user = require_user(request)
    return social_service.get_book_reviews_summary(book_id, viewer_user_id=user["id"])


@app.post("/api/books/{book_id}/reviews")
@app.put("/api/books/{book_id}/reviews")
@app.put("/api/books/{book_id}/reviews/me")
async def upsert_book_review_endpoint(book_id: str, payload: BookReviewPayload, request: Request):
    """Salva avaliação de 1 a 5 estrelas, resenha curta, favorito ou 'Quero ler' para um livro."""
    user = require_user(request)
    try:
        res = social_service.upsert_book_review(
            user_id=user["id"],
            book_id=book_id,
            rating=payload.rating,
            review_text=payload.review_text,
            is_favorite=payload.is_favorite,
            want_to_read=payload.want_to_read,
            is_public=payload.is_public,
        )
        return {"status": "success", **res}
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/books/{book_id}/shared-annotations")
async def list_book_shared_annotations_endpoint(book_id: str, request: Request):
    """Lista todas as anotações e citações compartilhadas de um livro."""
    user = require_user(request)
    items = social_service.list_shared_annotations(viewer_user_id=user["id"], book_id=book_id)
    return {"annotations": items, "count": len(items)}


@app.get("/api/shared-annotations")
@app.get("/api/social/annotations")
async def list_all_shared_annotations_endpoint(
    request: Request,
    book_id: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 50,
):
    """Lista anotações e citações compartilhadas na biblioteca."""
    user = require_user(request)
    items = social_service.list_shared_annotations(
        viewer_user_id=user["id"],
        book_id=book_id,
        author_user_id=user_id,
        limit=limit,
    )
    return {"annotations": items, "count": len(items)}


@app.post("/api/shared-annotations", status_code=status.HTTP_201_CREATED)
@app.post("/api/social/annotations", status_code=status.HTTP_201_CREATED)
async def create_shared_annotation_endpoint(
    payload: SharedAnnotationCreatePayload,
    request: Request,
):
    """Compartilha um trecho destacado ou anotação na biblioteca (com proteção anti-IDOR)."""
    user = require_user(request)
    try:
        created = social_service.share_annotation(
            user_id=user["id"],
            book_id=str(payload.book_id).strip(),
            quote_text=payload.quote_text,
            comment_text=payload.comment_text,
            content=payload.content,
            page_number=payload.page_number,
            chapter_name=payload.chapter_name,
            progress_locator=payload.progress_locator,
            color=payload.color,
            visibility=payload.visibility,
            note_id=str(payload.note_id) if payload.note_id is not None else None,
            source_note_id=str(payload.source_note_id) if payload.source_note_id is not None else None,
        )
        return {"status": "success", "annotation": created}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/shared-annotations/{shared_id}")
@app.put("/api/social/annotations/{shared_id}")
async def update_shared_annotation_endpoint(
    shared_id: int,
    payload: SharedAnnotationUpdatePayload,
    request: Request,
):
    """Atualiza o comentário ou visibilidade de uma anotação compartilhada pelo próprio autor."""
    user = require_user(request)
    try:
        updates = payload.model_dump(exclude_none=True)
        if "comment_text" not in updates and "content" in updates:
            updates["comment_text"] = updates["content"]
        updated = social_service.update_shared_annotation(
            shared_id=shared_id,
            user_id=user["id"],
            updates=updates,
        )
        return {"status": "success", "annotation": updated}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.delete("/api/shared-annotations/{shared_id}")
@app.delete("/api/social/annotations/{shared_id}")
async def delete_shared_annotation_endpoint(shared_id: int, request: Request):
    """Remove o compartilhamento de uma anotação (autor ou moderação admin em item público)."""
    user = require_user(request)
    try:
        social_service.delete_shared_annotation(
            shared_id=shared_id,
            user_id=user["id"],
            is_admin=(user.get("role") == "admin"),
        )
        return {"status": "success"}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/shared-annotations/{shared_id}/comments", status_code=status.HTTP_201_CREATED)
@app.post("/api/social/annotations/{shared_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment_to_shared_annotation_endpoint(
    shared_id: int,
    payload: AnnotationCommentPayload,
    request: Request,
):
    """Adiciona um comentário ou resposta a uma anotação compartilhada."""
    user = require_user(request)
    try:
        comment = social_service.add_annotation_comment(
            shared_annotation_id=shared_id,
            user_id=user["id"],
            content=payload.content,
            parent_id=payload.parent_id,
        )
        return {"status": "success", "comment": comment}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/annotation-comments/{comment_id}")
@app.put("/api/social/comments/{comment_id}")
async def edit_annotation_comment_endpoint(
    comment_id: int,
    payload: AnnotationCommentPayload,
    request: Request,
):
    """Edita o próprio comentário em uma anotação compartilhada."""
    user = require_user(request)
    try:
        updated = social_service.update_annotation_comment(
            comment_id=comment_id,
            user_id=user["id"],
            content=payload.content,
        )
        return {"status": "success", "comment": updated}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/annotation-comments/{comment_id}")
@app.delete("/api/social/comments/{comment_id}")
async def delete_annotation_comment_endpoint(comment_id: int, request: Request):
    """Exclui um comentário (próprio autor ou moderação admin)."""
    user = require_user(request)
    try:
        social_service.delete_annotation_comment(
            comment_id=comment_id,
            user_id=user["id"],
            is_admin=(user.get("role") == "admin"),
        )
        return {"status": "success"}
    except SocialPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/recommendations")
@app.get("/api/social/recommendations")
async def list_recommendations_endpoint(request: Request):
    """Lista recomendações de leitura recebidas e enviadas pelo usuário."""
    user = require_user(request)
    return social_service.list_user_recommendations(user["id"])


@app.post("/api/recommendations", status_code=status.HTTP_201_CREATED)
@app.post("/api/social/recommendations", status_code=status.HTTP_201_CREATED)
async def create_recommendation_endpoint(
    payload: RecommendationCreatePayload,
    request: Request,
):
    """Envia uma recomendação de livro para outro usuário ou para toda a comunidade."""
    user = require_user(request)
    try:
        rec = social_service.create_recommendation(
            from_user_id=user["id"],
            to_user_id=payload.to_user_id,
            recipient_user_id=payload.recipient_user_id,
            book_id=str(payload.book_id).strip(),
            message=payload.message,
        )
        return {"status": "success", "recommendation": rec}
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/social/feed")
async def get_nossa_biblioteca_feed_endpoint(request: Request):
    """Retorna o mural consolidado 'Nossa Biblioteca' respeitando as permissões de privacidade."""
    user = require_user(request)
    return social_service.get_nossa_biblioteca_feed(viewer_user_id=user["id"])


# =========================================================================
# ENDPOINTS REST: CLUBES DE LEITURA E LEITURA COLETIVA (v1.6.0)
# =========================================================================

@app.get("/api/reading-clubs")
async def list_reading_clubs_endpoint(
    request: Request,
    mine: bool = False,
    search: Optional[str] = None,
    include_archived: bool = False,
):
    """Lista clubes de leitura acessíveis ao usuário (públicos e privados onde é membro)."""
    user = require_user(request)
    clubs = clubs_service.list_clubs(
        user_id=user["id"],
        mine_only=mine,
        search=search,
        include_archived=include_archived,
    )
    return {"clubs": clubs, "count": len(clubs)}


@app.post("/api/reading-clubs", status_code=status.HTTP_201_CREATED)
async def create_reading_club_endpoint(payload: ClubCreatePayload, request: Request):
    """Cria um novo clube de leitura e associa o criador como 'owner'."""
    user = require_user(request)
    try:
        club = clubs_service.create_club(
            name=payload.name,
            description=payload.description,
            owner_id=user["id"],
            visibility=payload.visibility,
        )
        return {"status": "success", "club": club}
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/reading-clubs/invites/mine")
async def list_my_invites_endpoint(request: Request, status_filter: str = "pending"):
    """Lista convites recebidos pelo usuário autenticado (estática antes de /{club_id})."""
    user = require_user(request)
    invites = clubs_service.list_user_invites(user_id=user["id"], status=status_filter)
    return {"invites": invites, "count": len(invites)}


@app.post("/api/reading-clubs/invites/{invite_id}/respond")
async def respond_invite_endpoint(invite_id: str, payload: InviteResponsePayload, request: Request):
    """Aceita ou rejeita um convite para clube privado."""
    user = require_user(request)
    try:
        res = clubs_service.respond_to_invite(
            invite_id=invite_id,
            user_id=user["id"],
            accept=payload.accept,
        )
        return {"status": "success", "result": res}
    except InviteNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/reading-clubs/invites/{invite_id}")
async def cancel_invite_endpoint(invite_id: str, request: Request):
    """Cancela um convite pendente (remetente ou proprietário/moderador do clube)."""
    user = require_user(request)
    try:
        res = clubs_service.cancel_invite(invite_id=invite_id, acting_user_id=user["id"])
        return {"status": "success", "result": res}
    except InviteNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/reading-clubs/{club_id}")
async def get_reading_club_endpoint(club_id: str, request: Request):
    """Retorna dados completos de um clube de leitura (anti-enumeração para clubes privados)."""
    user = require_user(request)
    try:
        club = clubs_service.get_club(club_id=club_id, user_id=user["id"])
        return {"club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/reading-clubs/{club_id}")
async def update_reading_club_endpoint(club_id: str, payload: ClubUpdatePayload, request: Request):
    """Edita metadados do clube (apenas proprietário ou admin)."""
    user = require_user(request)
    try:
        club = clubs_service.update_club(
            club_id=club_id,
            user_id=user["id"],
            name=payload.name,
            description=payload.description,
            visibility=payload.visibility,
        )
        return {"status": "success", "club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/archive")
@app.patch("/api/reading-clubs/{club_id}/archive")
async def archive_reading_club_endpoint(club_id: str, request: Request, payload: Optional[ClubArchivePayload] = None):
    """Arquiva ou desarquiva um clube de leitura preservando o histórico."""
    user = require_user(request)
    archive = payload.archive if payload is not None else True
    try:
        club = clubs_service.archive_club(club_id=club_id, user_id=user["id"], archive=archive)
        return {"status": "success", "club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/join")
async def join_reading_club_endpoint(club_id: str, request: Request):
    """Permite entrada em clube público ou clube privado com convite pendente."""
    user = require_user(request)
    try:
        club = clubs_service.join_club(club_id=club_id, user_id=user["id"])
        return {"status": "success", "club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/leave")
async def leave_reading_club_endpoint(club_id: str, request: Request):
    """Permite saída de membro do clube com validação de titularidade."""
    user = require_user(request)
    try:
        res = clubs_service.leave_club(club_id=club_id, user_id=user["id"])
        return {"status": "success", "result": res}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/reading-clubs/{club_id}/members")
async def list_club_members_endpoint(club_id: str, request: Request):
    """Lista todos os participantes de um clube e seus papéis."""
    user = require_user(request)
    try:
        members = clubs_service.list_club_members(club_id=club_id, user_id=user["id"])
        return {"members": members, "count": len(members)}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/reading-clubs/{club_id}/members/{target_user_id}")
async def update_club_member_role_endpoint(
    club_id: str,
    target_user_id: int,
    payload: MemberRoleUpdatePayload,
    request: Request,
):
    """Atualiza papel de participante (promove ou rebaixa entre moderator e member)."""
    user = require_user(request)
    try:
        res = clubs_service.update_member_role(
            club_id=club_id,
            acting_user_id=user["id"],
            target_user_id=target_user_id,
            new_role=payload.role,
        )
        return {"status": "success", "result": res}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/reading-clubs/{club_id}/members/{target_user_id}")
async def remove_club_member_endpoint(club_id: str, target_user_id: int, request: Request):
    """Remove participante do clube (proprietário ou moderador)."""
    user = require_user(request)
    try:
        res = clubs_service.remove_club_member(
            club_id=club_id,
            acting_user_id=user["id"],
            target_user_id=target_user_id,
        )
        return {"status": "success", "result": res}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/transfer-ownership")
async def transfer_club_ownership_endpoint(
    club_id: str,
    payload: OwnershipTransferPayload,
    request: Request,
):
    """Transfere a titularidade do clube para outro membro."""
    user = require_user(request)
    try:
        club = clubs_service.transfer_club_ownership(
            club_id=club_id,
            current_owner_id=user["id"],
            new_owner_id=payload.new_owner_id,
        )
        return {"status": "success", "club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_club_invite_endpoint(
    club_id: str,
    payload: ClubInvitePayload,
    request: Request,
):
    """Cria convite para usuário ingressar em clube privado."""
    user = require_user(request)
    try:
        res = clubs_service.invite_user(
            club_id=club_id,
            inviter_id=user["id"],
            invitee_id=payload.invitee_id,
        )
        return {"status": "success", "result": res}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/reading-clubs/{club_id}/invites")
async def list_club_invites_endpoint(club_id: str, request: Request):
    """Lista convites pendentes de um clube (apenas proprietário e moderadores)."""
    user = require_user(request)
    try:
        invites = clubs_service.list_club_invites(club_id=club_id, user_id=user["id"])
        return {"invites": invites, "count": len(invites)}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@app.get("/api/reading-clubs/{club_id}/reads")
async def list_club_reads_endpoint(club_id: str, request: Request, status_filter: Optional[str] = None):
    """Lista todas as leituras coletivas do clube."""
    user = require_user(request)
    try:
        reads = clubs_service.list_club_reads(
            club_id=club_id,
            user_id=user["id"],
            status=status_filter,
        )
        return {"reads": reads, "count": len(reads)}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/reading-clubs/{club_id}/reads", status_code=status.HTTP_201_CREATED)
async def create_collective_read_endpoint(
    club_id: str,
    payload: CollectiveReadCreatePayload,
    request: Request,
):
    """Cria uma nova leitura coletiva no clube associada a um livro."""
    user = require_user(request)
    try:
        read = clubs_service.create_collective_read(
            club_id=club_id,
            user_id=user["id"],
            book_id=payload.book_id,
            title=payload.title,
            description=payload.description,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status=payload.status,
        )
        return {"status": "success", "read": read}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# -------------------------------------------------------------------------
# ENDPOINTS REST: LEITURAS COLETIVAS (Rotas estáticas antes de {read_id})
# -------------------------------------------------------------------------

@app.get("/api/collective-reads/mine")
async def list_my_collective_reads_endpoint(request: Request, status_filter: Optional[str] = None):
    """Lista todas as leituras coletivas nas quais o usuário autenticado participa."""
    user = require_user(request)
    reads = clubs_service.list_user_collective_reads(user_id=user["id"], status=status_filter)
    return {"reads": reads, "count": len(reads)}


@app.get("/api/collective-reads/{read_id}")
async def get_collective_read_endpoint(read_id: str, request: Request):
    """Retorna detalhes de uma leitura coletiva, metadados do livro e participantes."""
    user = require_user(request)
    try:
        read = clubs_service.get_collective_read(read_id=read_id, user_id=user["id"])
        return {"read": read}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/collective-reads/{read_id}")
async def update_collective_read_endpoint(
    read_id: str,
    payload: CollectiveReadUpdatePayload,
    request: Request,
):
    """Edita dados e status de uma leitura coletiva (proprietário, moderadores ou criador)."""
    user = require_user(request)
    try:
        read = clubs_service.update_collective_read(
            read_id=read_id,
            user_id=user["id"],
            title=payload.title,
            description=payload.description,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status=payload.status,
        )
        return {"status": "success", "read": read}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/collective-reads/{read_id}/join")
async def join_collective_read_endpoint(read_id: str, request: Request):
    """Inscreve o usuário na leitura coletiva (requer ser membro do clube)."""
    user = require_user(request)
    try:
        read = clubs_service.join_collective_read(read_id=read_id, user_id=user["id"])
        return {"status": "success", "read": read}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ClubPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InvalidOperationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/collective-reads/{read_id}/leave")
async def leave_collective_read_endpoint(read_id: str, request: Request):
    """Permite a saída de um participante da leitura coletiva."""
    user = require_user(request)
    try:
        res = clubs_service.leave_collective_read(read_id=read_id, user_id=user["id"])
        return {"status": "success", "result": res}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  # Estritamente 1 worker para baixo consumo de memória
        reload=False,
    )
