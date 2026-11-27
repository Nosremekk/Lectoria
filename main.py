"""
Ponto de entrada da aplicação FastAPI do Lectoria.
Servidor HTTP leve para o leitor e gerenciador pessoal de livros digitais.
Suporta streaming com HTTP Range, downloads, múltiplas fontes e perfis com isolamento de dados.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from email.utils import formatdate
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

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
from http_cache_service import (
    AssetManifest,
    HashedStaticFiles,
    SelectiveGZipMiddleware,
    etag_matches_if_none_match,
)
import document_optimization_service
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
    get_taxonomy_facets,
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
    AnnotationPermissionError,
    MAX_BOOKMARK_LABEL_LEN,
    MAX_CHAPTER_LABEL_LEN,
    MAX_EPUB_CFI_LEN,
    MAX_NOTE_CONTENT_LEN,
    MAX_RECTS_PER_HIGHLIGHT,
    MAX_SELECTED_TEXT_LEN,
    MAX_STROKES_PER_DRAWING,
    MAX_TEXT_CONTEXT_LEN,
    create_bookmark,
    create_highlight,
    create_note,
    delete_bookmark,
    delete_highlight,
    delete_note,
    delete_page_drawing,
    get_book_annotations_summary,
    get_highlights,
    get_notes,
    get_page_drawing,
    get_page_drawings_bundle,
    list_bookmarks,
    save_page_drawing,
    set_highlight_visibility,
    set_note_visibility,
    set_page_drawing_visibility,
    update_note,
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
import shared_reading_service
from shared_reading_service import (
    GoalNotFoundError,
    PostNotFoundError,
    QuoteNotFoundError,
    TopicNotFoundError,
)
import knowledge_service
from knowledge_service import (
    AUX_SEARCH_LIMIT,
    DEFAULT_CONCEPT_COLOR,
    DEFAULT_GRAPH_DEPTH,
    DEFAULT_GRAPH_MAX_EDGES,
    DEFAULT_GRAPH_MAX_NODES,
    DEFAULT_PAGE_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    HARD_MAX_GRAPH_EDGES,
    HARD_MAX_GRAPH_NODES,
    MAX_COLOR_LEN,
    MAX_CONCEPT_DESC_LEN,
    MAX_CONCEPT_NAME_LEN,
    MAX_LINK_RATIONALE_LEN,
    MAX_PAGE_LIMIT,
    MAX_REFLECTION_BODY_LEN,
    MAX_REFLECTION_TITLE_LEN,
    MAX_SEARCH_LIMIT,
    MAX_SEARCH_QUERY_LEN,
    MAX_TAG_LEN,
    MAX_TAGS_COUNT,
    KnowledgeConflictError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgePermissionError,
    KnowledgeValidationError,
)
import knowledge_export_service
from knowledge_export_service import (
    KnowledgeExportBusyError,
    KnowledgeExportLimitError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Executado na inicialização e encerramento da aplicação."""
    # Garante que as tabelas SQLite existem e migrações estão aplicadas
    init_db()
    # Recupera tarefas em segundo plano que foram interrompidas por reinício anterior
    automation_service.recover_interrupted_jobs_on_startup()
    document_optimization_service.recover_on_startup()
    # Executa uma varredura inicial rápida para registrar arquivos existentes
    scan_library()
    # Inicia loop leve do agendador e worker cooperativo de otimização de documentos
    automation_service.scheduler_loop.start()
    document_optimization_service.start_background_worker()
    try:
        yield
    finally:
        document_optimization_service.stop_background_worker()
        automation_service.scheduler_loop.stop()


app = FastAPI(
    title=config.APP_NAME,
    description="Servidor pessoal de livros digitais em PDF",
    version=config.APP_VERSION,
    lifespan=lifespan,
)

CSRF_COOKIE_NAME = "lectoria_csrf"
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}

DEFAULT_HTML_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: blob:; "
    "connect-src 'self' blob:; "
    "worker-src 'self' blob:; "
    "frame-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'"
)
DEFAULT_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def is_effective_https_request(request: Request) -> bool:
    """
    Determina se a requisição efetiva do cliente utilizou HTTPS.
    Confia em X-Forwarded-Proto exclusivamente quando o peer imediato pertence
    a config.FORWARDED_ALLOW_IPS (impedindo spoofing por clientes não confiáveis).
    """
    if request.url.scheme.lower() == "https":
        return True
    peer_host = request.client.host if request.client else None
    if not config.is_trusted_proxy_ip(peer_host):
        return False
    raw_proto = request.headers.get("x-forwarded-proto", "")
    if not raw_proto:
        return False
    first_proto = raw_proto.split(",")[0].strip().lower()
    return first_proto == "https"


def should_use_secure_cookies(request: Request) -> bool:
    """Resolve o atributo Secure dos cookies conforme LECTORIA_SECURE_COOKIES."""
    mode = config.SECURE_COOKIES_MODE
    if mode == "true":
        return True
    if mode == "false":
        return False
    return is_effective_https_request(request)


def get_effective_client_ip(request: Request, allow_testclient_remote_hint: bool = False) -> Optional[str]:
    """
    Retorna o IP efetivo do cliente respeitando X-Forwarded-For somente quando
    proveniente de proxy explicitamente autorizado em config.FORWARDED_ALLOW_IPS.
    """
    peer_host = request.client.host if request.client else None
    raw_xff = request.headers.get("x-forwarded-for", "").strip()
    if raw_xff:
        first_ip = raw_xff.split(",")[0].strip()
        if config.is_trusted_proxy_ip(peer_host):
            return first_ip or peer_host
        if (
            allow_testclient_remote_hint
            and peer_host == "testclient"
            and first_ip not in ("127.0.0.1", "::1", "localhost")
        ):
            return first_ip
    return peer_host


@app.middleware("http")
async def csrf_and_security_middleware(request: Request, call_next):
    """
    Middleware de segurança:
    1. Valida correspondência de Host com Origin/Referer em requisições mutantes para barrar CSRF cross-origin.
    2. Aplica validação Double Submit Cookie CSRF caso o cookie lectoria_csrf esteja presente.
    3. Aplica cabeçalhos defensivos (Permissions-Policy, CSP configurável e HSTS opcional em HTTPS).
    4. Emite cookie lectoria_csrf transparente apenas em páginas HTML (nunca em /static/*).
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
        # Se o cliente possui o cookie lectoria_csrf, valida o token enviado no cabeçalho (ou query string legada fora de /api/knowledge)
        expected_csrf = request.cookies.get(CSRF_COOKIE_NAME)
        if expected_csrf:
            is_knowledge_route = request.url.path.startswith("/api/knowledge")
            if is_knowledge_route and request.query_params.get("csrf_token"):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "O envio de token CSRF via URL não é permitido nas rotas de conhecimento. Utilize o cabeçalho x-csrf-token."},
                    headers={"Cache-Control": "private, no-store"},
                )
            provided_csrf = (
                request.headers.get("x-csrf-token")
                if is_knowledge_route
                else (
                    request.headers.get("x-csrf-token")
                    or request.query_params.get("csrf_token")
                )
            )
            if not provided_csrf or not secrets.compare_digest(provided_csrf, expected_csrf):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Token CSRF inválido ou ausente."},
                    headers={"Cache-Control": "private, no-store"},
                )

    response = await call_next(request)

    path = request.url.path
    is_html_page = (
        request.method in SAFE_HTTP_METHODS
        and not path.startswith("/api/")
        and not path.startswith("/static/")
    )

    # 3. Cabeçalhos fundamentais de proteção contra MIME sniffing, clickjacking, vazamento de referer e cache privado de conhecimento
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if path.startswith("/api/knowledge"):
        response.headers["Cache-Control"] = "private, no-store"

    if is_html_page:
        response.headers.setdefault("Permissions-Policy", DEFAULT_PERMISSIONS_POLICY)
        csp_mode = config.CSP_MODE
        if csp_mode == "report-only":
            response.headers.setdefault("Content-Security-Policy-Report-Only", DEFAULT_HTML_CSP)
        elif csp_mode == "enforce":
            response.headers.setdefault("Content-Security-Policy", DEFAULT_HTML_CSP)

    if config.HSTS_ENABLED and is_effective_https_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    # 4. Emissão transparente do cookie CSRF apenas em páginas HTML (nunca em /static/* nem /api/*)
    if is_html_page and CSRF_COOKIE_NAME not in request.cookies:
        new_csrf = secrets.token_urlsafe(32)
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=new_csrf,
            httponly=False,  # Legível pelo JavaScript para inclusão no cabeçalho X-CSRF-Token
            samesite="lax",
            secure=should_use_secure_cookies(request),
            path="/",
        )

    return response

# Compressão HTTP seletiva (gzip nível 5 para HTML, CSS, JS e JSON elegíveis)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=500, compresslevel=5)

# Manifesto em memória baseado em hash SHA-256 de conteúdo + StaticFiles com immutable estrito
static_path = config.BASE_DIR / "static"
asset_manifest = AssetManifest(static_path)
app.mount("/static", HashedStaticFiles(directory=str(static_path), manifest=asset_manifest), name="static")


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
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, description="Número da página")
    color: str = Field(default="#fef08a", max_length=32, description="Cor de destaque em hexadecimal")
    rects: list[dict] = Field(default_factory=list, max_length=MAX_RECTS_PER_HIGHLIGHT, description="Retângulos normalizados de seleção")
    selected_text: str = Field(min_length=1, max_length=MAX_SELECTED_TEXT_LEN, description="Texto selecionado")
    text_context: Optional[str] = Field(default=None, max_length=MAX_TEXT_CONTEXT_LEN, description="Contexto do texto")
    epub_cfi: Optional[str] = Field(default=None, max_length=MAX_EPUB_CFI_LEN, description="Âncora EPUB CFI")
    chapter_label: Optional[str] = Field(default=None, max_length=MAX_CHAPTER_LABEL_LEN, description="Rótulo do capítulo")
    visibility: Literal["private", "public"] = Field(default="private", description="Visibilidade do destaque")


class NoteCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, description="Número da página")
    content: str = Field(min_length=1, max_length=MAX_NOTE_CONTENT_LEN, description="Conteúdo da anotação")
    color: Optional[str] = Field(default="#fef08a", max_length=32, description="Cor temática")
    highlight_id: Optional[str] = Field(default=None, max_length=128, description="ID do destaque vinculado se houver")
    epub_cfi: Optional[str] = Field(default=None, max_length=MAX_EPUB_CFI_LEN, description="Âncora EPUB CFI")
    chapter_label: Optional[str] = Field(default=None, max_length=MAX_CHAPTER_LABEL_LEN, description="Rótulo do capítulo")
    visibility: Literal["private", "public"] = Field(default="private", description="Visibilidade da nota")


class NoteUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_NOTE_CONTENT_LEN, description="Conteúdo da anotação")
    color: Optional[str] = Field(default=None, max_length=32, description="Cor temática")
    visibility: Optional[Literal["private", "public"]] = Field(default=None, description="Visibilidade opcional da nota")
    chapter_label: Optional[str] = Field(default=None, max_length=MAX_CHAPTER_LABEL_LEN, description="Rótulo do capítulo")


class DrawingSavePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strokes: list[dict] = Field(max_length=MAX_STROKES_PER_DRAWING, description="Array de traços vetoriais com pontos normalizados")
    visibility: Optional[Literal["private", "public"]] = Field(default=None, description="Visibilidade opcional do desenho")


class AnnotationVisibilityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: Literal["private", "public"] = Field(description="Visibilidade da anotação: 'private' ou 'public'")


class BookmarkCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, description="Número da página")
    epub_cfi: Optional[str] = Field(default=None, max_length=MAX_EPUB_CFI_LEN, description="Âncora EPUB CFI")
    chapter_label: Optional[str] = Field(default=None, max_length=MAX_CHAPTER_LABEL_LEN, description="Rótulo do capítulo")
    label: Optional[str] = Field(default="", max_length=MAX_BOOKMARK_LABEL_LEN, description="Rótulo opcional do marcador")


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


def _validate_epub_cfi_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    clean = v.strip()
    if not clean:
        return None
    if not (clean.startswith("epubcfi(") and clean.endswith(")")) or "<" in clean or ">" in clean:
        raise ValueError("Localizador EPUB CFI inválido.")
    return clean


def _validate_iso_date_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    clean = v.strip()
    if not clean:
        return None
    try:
        datetime.strptime(clean, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Data inválida. Utilize o formato YYYY-MM-DD.") from exc
    return clean


class SharedTopicCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    chapter_ref: Optional[str] = Field(default=None, max_length=150)
    page_start: Optional[int] = Field(default=None, ge=1, le=100000)
    page_end: Optional[int] = Field(default=None, ge=1, le=100000)
    epub_cfi: Optional[str] = Field(default=None, max_length=500)
    is_spoiler: bool = False

    @field_validator("title", "content")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O campo não pode ser vazio.")
        return clean

    @field_validator("epub_cfi")
    @classmethod
    def _check_cfi(cls, v: Optional[str]) -> Optional[str]:
        return _validate_epub_cfi_value(v)

    @model_validator(mode="after")
    def _check_page_range(self):
        if self.page_start is not None and self.page_end is not None and self.page_end < self.page_start:
            raise ValueError("page_end não pode ser menor que page_start.")
        return self


class SharedTopicUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=10000)
    chapter_ref: Optional[str] = Field(default=None, max_length=150)
    page_start: Optional[int] = Field(default=None, ge=1, le=100000)
    page_end: Optional[int] = Field(default=None, ge=1, le=100000)
    epub_cfi: Optional[str] = Field(default=None, max_length=500)
    is_spoiler: Optional[bool] = None

    @field_validator("title", "content")
    @classmethod
    def _strip_optional_non_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("O campo não pode ser vazio.")
        return clean

    @field_validator("epub_cfi")
    @classmethod
    def _check_cfi(cls, v: Optional[str]) -> Optional[str]:
        return _validate_epub_cfi_value(v)

    @model_validator(mode="after")
    def _check_page_range(self):
        if self.page_start is not None and self.page_end is not None and self.page_end < self.page_start:
            raise ValueError("page_end não pode ser menor que page_start.")
        return self


class SharedTopicModeratePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_pinned: Optional[bool] = None
    is_locked: Optional[bool] = None


class SharedPostCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=5000)
    parent_post_id: Optional[str] = Field(default=None, max_length=64)
    is_spoiler: bool = False

    @field_validator("content")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O conteúdo da resposta não pode ser vazio.")
        return clean


class SharedPostUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    is_spoiler: Optional[bool] = None

    @field_validator("content")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("O conteúdo da resposta não pode ser vazio.")
        return clean


class SharedGoalCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    target_date: Optional[str] = Field(default=None, max_length=32)
    chapter_ref: Optional[str] = Field(default=None, max_length=150)
    target_page: Optional[int] = Field(default=None, ge=1, le=100000)
    display_order: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O título da meta não pode ser vazio.")
        return clean

    @field_validator("target_date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date_value(v)


class SharedGoalUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    target_date: Optional[str] = Field(default=None, max_length=32)
    chapter_ref: Optional[str] = Field(default=None, max_length=150)
    target_page: Optional[int] = Field(default=None, ge=1, le=100000)
    display_order: Optional[int] = Field(default=None, ge=0, le=10000)
    is_archived: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("O título da meta não pode ser vazio.")
        return clean

    @field_validator("target_date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date_value(v)


class SharedGoalProgressPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "in_progress", "completed"]


class SharedQuoteCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_text: Optional[str] = Field(default=None, max_length=1500)
    comment_text: Optional[str] = Field(default=None, max_length=2000)
    chapter_name: Optional[str] = Field(default=None, max_length=150)
    page_number: Optional[int] = Field(default=None, ge=1, le=100000)
    epub_cfi: Optional[str] = Field(default=None, max_length=500)
    is_spoiler: bool = False
    note_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("epub_cfi")
    @classmethod
    def _check_cfi(cls, v: Optional[str]) -> Optional[str]:
        return _validate_epub_cfi_value(v)

    @model_validator(mode="after")
    def _check_quote_or_note(self):
        has_note = bool(self.note_id and self.note_id.strip())
        has_quote = bool(self.quote_text and self.quote_text.strip())
        if not has_note and not has_quote:
            raise ValueError("Informe quote_text ou note_id para compartilhar uma citação.")
        return self


# =========================================================================
# MODELOS DE DADOS PARA CONHECIMENTO CONECTADO / SEÇÃO ATLAS (v1.9.0)
# =========================================================================

KnowledgeEntityTypeLiteral = Literal["concept", "reflection", "highlight", "note", "book"]
KnowledgeRelationTypeLiteral = Literal[
    "complements",
    "contradicts",
    "exemplifies",
    "grounds",
    "develops",
    "references",
    "questions",
    "relates_to",
]
KnowledgeDirectionLiteral = Literal["outgoing", "incoming", "both"]


class ConceptCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_CONCEPT_NAME_LEN, description="Nome do conceito")
    description: str = Field(default="", max_length=MAX_CONCEPT_DESC_LEN, description="Definição ou descrição autoral")
    tags: List[str] = Field(default_factory=list, max_length=MAX_TAGS_COUNT, description="Etiquetas leves")
    color: Optional[str] = Field(default=DEFAULT_CONCEPT_COLOR, max_length=MAX_COLOR_LEN, description="Cor hexadecimal do conceito")
    is_archived: bool = Field(default=False, description="Estado de arquivamento")

    @field_validator("name")
    @classmethod
    def _check_name_non_empty(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O nome do conceito não pode ser vazio.")
        return clean


class ConceptUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_CONCEPT_NAME_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_CONCEPT_DESC_LEN)
    tags: Optional[List[str]] = Field(default=None, max_length=MAX_TAGS_COUNT)
    color: Optional[str] = Field(default=None, max_length=MAX_COLOR_LEN)
    is_archived: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _check_optional_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("O nome do conceito não pode ser vazio.")
        return clean

    @model_validator(mode="after")
    def _check_non_null_fields_if_explicitly_set(self):
        for field_name in ("name", "description", "tags", "color", "is_archived"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"O campo '{field_name}' não pode ser nulo quando enviado na atualização.")
        return self


class ConceptResponseModel(BaseModel):
    id: str
    user_id: int
    name: str
    normalized_name: str
    description: str
    tags: List[str]
    color: str
    is_archived: bool
    links_count: int = 0
    created_at: str
    updated_at: str


class ConceptListResponseModel(BaseModel):
    items: List[ConceptResponseModel]
    total: int
    limit: int
    offset: int
    has_more: bool


class ReflectionCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_REFLECTION_TITLE_LEN, description="Título da reflexão")
    body_markdown: str = Field(default="", max_length=MAX_REFLECTION_BODY_LEN, description="Corpo textual em Markdown puro (não renderizado)")
    primary_book_id: Optional[str] = Field(default=None, max_length=128, description="ID opcional da obra principal associada")
    tags: List[str] = Field(default_factory=list, max_length=MAX_TAGS_COUNT, description="Etiquetas leves")
    is_pinned: bool = Field(default=False, description="Indica se a reflexão está fixada no topo")
    is_archived: bool = Field(default=False, description="Estado de arquivamento")

    @field_validator("title")
    @classmethod
    def _check_title_non_empty(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O título da reflexão não pode ser vazio.")
        return clean


class ReflectionUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=MAX_REFLECTION_TITLE_LEN)
    body_markdown: Optional[str] = Field(default=None, max_length=MAX_REFLECTION_BODY_LEN)
    primary_book_id: Optional[str] = Field(default=None, max_length=128)
    tags: Optional[List[str]] = Field(default=None, max_length=MAX_TAGS_COUNT)
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None
    expected_updated_at: Optional[str] = Field(default=None, min_length=1, max_length=64)

    @field_validator("title")
    @classmethod
    def _check_optional_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("O título da reflexão não pode ser vazio.")
        return clean

    @model_validator(mode="after")
    def _check_non_null_fields_except_primary_book(self):
        # primary_book_id pode ser explicitamente None para desassociar o livro principal;
        # os demais campos não podem ser nulos quando enviados explicitamente.
        for field_name in ("title", "body_markdown", "tags", "is_pinned", "is_archived", "expected_updated_at"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"O campo '{field_name}' não pode ser nulo quando enviado na atualização.")
        return self


class ReflectionResponseModel(BaseModel):
    id: str
    user_id: int
    title: str
    body_markdown: str
    primary_book_id: Optional[str] = None
    primary_book_title: Optional[str] = None
    primary_book_available: Optional[bool] = None
    tags: List[str]
    is_pinned: bool
    is_archived: bool
    references_count: int = 0
    backlinks_count: int = 0
    created_at: str
    updated_at: str


class ReflectionListResponseModel(BaseModel):
    items: List[ReflectionResponseModel]
    total: int
    limit: int
    offset: int
    has_more: bool


class KnowledgeEntityEndpointModel(BaseModel):
    """Representa uma entidade disponível ou uma referência indisponível mascarada com segurança."""
    type: KnowledgeEntityTypeLiteral
    id: str
    available: bool
    status: Literal["available", "unavailable"]
    label: str
    is_own: Optional[bool] = None
    user_id: Optional[int] = None
    author_display_name: Optional[str] = None
    title: Optional[str] = None
    excerpt: Optional[str] = None
    color: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    book_id: Optional[str] = None
    book_title: Optional[str] = None
    book_format: Optional[str] = None
    page: Optional[int] = None
    epub_cfi: Optional[str] = None
    chapter_label: Optional[str] = None
    visibility: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class KnowledgeLinkCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: KnowledgeEntityTypeLiteral
    source_id: str = Field(min_length=1, max_length=128)
    target_type: KnowledgeEntityTypeLiteral
    target_id: str = Field(min_length=1, max_length=128)
    relation_type: KnowledgeRelationTypeLiteral = Field(default="relates_to")
    rationale: str = Field(default="", max_length=MAX_LINK_RATIONALE_LEN)

    @field_validator("source_id", "target_id")
    @classmethod
    def _strip_ids(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("O identificador da entidade não pode ser vazio.")
        return clean


class KnowledgeLinkUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: Optional[KnowledgeRelationTypeLiteral] = None
    rationale: Optional[str] = Field(default=None, max_length=MAX_LINK_RATIONALE_LEN)

    @model_validator(mode="after")
    def _check_fields_not_null(self):
        if not self.model_fields_set:
            raise ValueError("Informe ao menos 'relation_type' ou 'rationale' para atualizar a conexão.")
        for field_name in ("relation_type", "rationale"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"O campo '{field_name}' não pode ser nulo quando enviado na atualização.")
        return self


class KnowledgeLinkResponseModel(BaseModel):
    id: str
    user_id: int
    source_type: KnowledgeEntityTypeLiteral
    source_id: str
    source: KnowledgeEntityEndpointModel
    target_type: KnowledgeEntityTypeLiteral
    target_id: str
    target: KnowledgeEntityEndpointModel
    relation_type: KnowledgeRelationTypeLiteral
    relation_label: str
    outgoing_label: str
    incoming_label: str
    direction: Literal["outgoing", "incoming"]
    counterpart: KnowledgeEntityEndpointModel
    rationale: str
    created_at: str
    updated_at: str


class KnowledgeLinkListResponseModel(BaseModel):
    items: List[KnowledgeLinkResponseModel]
    total: int
    limit: int
    offset: int
    has_more: bool


class KnowledgeEntityConnectionsResponseModel(BaseModel):
    entity: KnowledgeEntityEndpointModel
    direction: KnowledgeDirectionLiteral
    outgoing_links: List[KnowledgeLinkResponseModel]
    incoming_backlinks: List[KnowledgeLinkResponseModel]
    total_outgoing: int
    total_incoming: int
    limit: int
    offset: int
    has_more_outgoing: bool
    has_more_incoming: bool


class KnowledgeLookupItemModel(BaseModel):
    type: KnowledgeEntityTypeLiteral
    id: str
    label: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    excerpt: Optional[str] = None
    color: Optional[str] = None
    book_id: Optional[str] = None
    book_title: Optional[str] = None
    page: Optional[int] = None
    epub_cfi: Optional[str] = None
    chapter_label: Optional[str] = None
    is_own: bool
    visibility: str


class KnowledgeLookupResponseModel(BaseModel):
    items: List[KnowledgeLookupItemModel]
    count: int
    limit: int
    query: str
    entity_type: Optional[KnowledgeEntityTypeLiteral] = None
    book_id: Optional[str] = None


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

def _build_rendered_html_response(html_file: Path, request: Request) -> Response:
    """Entrega página HTML com URLs de assets versionadas por hash de conteúdo e revalidação via ETag."""
    rendered_bytes, etag = asset_manifest.render_html(html_file, config.APP_VERSION)
    headers = {
        "Cache-Control": "no-cache",
        "ETag": etag,
    }
    if etag_matches_if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if request.method.upper() == "HEAD":
        headers["Content-Length"] = str(len(rendered_bytes))
        return Response(content=b"", media_type="text/html; charset=utf-8", headers=headers)
    return Response(content=rendered_bytes, media_type="text/html; charset=utf-8", headers=headers)


@app.api_route("/", methods=["GET", "HEAD"])
def serve_library_page(request: Request):
    """Entrega a página principal da biblioteca."""
    return _build_rendered_html_response(static_path / "index.html", request)


@app.api_route("/reader/{book_id}", methods=["GET", "HEAD"])
def serve_reader_page(book_id: str, request: Request):
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
    return _build_rendered_html_response(static_path / "reader.html", request)


@app.api_route("/reader.html", methods=["GET", "HEAD"])
def serve_reader_page_html_alias(request: Request, id: Optional[str] = None):
    """Redireciona links com parâmetro ?id=... para o caminho oficial /reader/{id}."""
    if id:
        return RedirectResponse(url=f"/reader/{id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return _build_rendered_html_response(static_path / "reader.html", request)


# =========================================================================
# ENDPOINTS DE AUTENTICAÇÃO E PERFIS DE USUÁRIO
# =========================================================================

@app.get("/api/auth/status")
def auth_status(request: Request):
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
def setup_initial_admin(payload: SetupPayload, request: Request, response: Response):
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
            secure=should_use_secure_cookies(request),
            path="/",
            max_age=7 * 24 * 3600,
        )
        return {"status": "success", "user": user, "token": token}
    except (ValueError, UserAlreadyExistsError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/auth/login")
def login(payload: LoginPayload, request: Request, response: Response):
    """Autentica o usuário e inicializa cookie de sessão HTTP seguro."""
    client_ip = get_effective_client_ip(request) or ""
    try:
        user = authenticate_user(payload.username, payload.password, client_ip=client_ip)
        token = create_session(user["id"])
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=should_use_secure_cookies(request),
            path="/",
            max_age=7 * 24 * 3600,
        )
        return {"status": "success", "user": user, "token": token}
    except RateLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    """Encerra a sessão ativa e limpa os cookies."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers.get("authorization", "")[7:].strip()

    if token:
        try:
            u = get_user_from_session(token)
            if u and u.get("id") is not None:
                document_optimization_service.release_user_sessions(u["id"])
        except Exception:
            pass
        destroy_session(token)

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=should_use_secure_cookies(request),
    )
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "private, no-store"
    return {"status": "success", "message": "Sessão finalizada com sucesso."}


@app.get("/api/auth/me")
def get_me(request: Request):
    """Retorna os dados do usuário autenticado."""
    user = require_user(request)
    return {"user": user}


@app.get("/api/auth/csrf")
def get_csrf_token_endpoint(request: Request, response: Response):
    """Retorna o token CSRF atual ou gera um novo caso não exista."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            httponly=False,
            samesite="lax",
            secure=should_use_secure_cookies(request),
            path="/",
        )
    return {"csrf_token": token}


# =========================================================================
# ENDPOINTS ADMINISTRATIVOS: GERENCIAMENTO DE USUÁRIOS
# =========================================================================

@app.get("/api/users")
def list_users_endpoint(request: Request):
    """Lista todos os usuários (administrador apenas)."""
    require_admin(request)
    return {"users": list_users()}


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user_endpoint(payload: UserCreatePayload, request: Request):
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
def update_user_endpoint(user_id: int, payload: UserUpdatePayload, request: Request):
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
def delete_user_endpoint(user_id: int, request: Request):
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
def list_sources_endpoint(request: Request):
    """Lista todas as fontes cadastradas (administrador apenas)."""
    require_admin(request)
    return {"sources": list_library_sources()}


@app.get("/api/sources/browse")
def browse_sources_directories_endpoint(request: Request, path: Optional[str] = None):
    """
    Navegador visual seguro de diretórios do servidor (administrador apenas).
    Permite explorar pastas autorizadas sem digitar caminhos manualmente.
    """
    require_admin(request)
    client_host = get_effective_client_ip(request, allow_testclient_remote_hint=True)
    try:
        return browse_server_directories(raw_path=path, client_host=client_host)
    except InvalidSourceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/sources/pick-native")
def pick_native_directory_endpoint(
    request: Request,
    payload: Optional[NativePickerPayload] = None,
):
    """
    Aciona o seletor nativo de diretórios do sistema operacional no servidor local.
    Bloqueado para acessos remotos ou ambientes sem sessão gráfica.
    """
    require_admin(request)
    client_host = get_effective_client_ip(request, allow_testclient_remote_hint=True)
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
def add_source_endpoint(payload: SourceCreatePayload, request: Request):
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
def toggle_source_active_endpoint(source_id: int, payload: SourceActivePayload, request: Request):
    """Ativa ou desativa uma fonte."""
    require_admin(request)
    try:
        updated = set_source_active(source_id, payload.is_active)
        return {"status": "success", "source": updated}
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.delete("/api/sources/{source_id}")
def remove_source_endpoint(source_id: int, request: Request):
    """Remove uma fonte de biblioteca (nunca apaga os PDFs físicos)."""
    require_admin(request)
    try:
        delete_library_source(source_id)
        return {"status": "success", "message": "Fonte removida com sucesso."}
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/sources/{source_id}/scan")
def scan_single_source_endpoint(source_id: int, request: Request):
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
def list_books(
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
    compact: bool = False,
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
        compact=compact,
    )
    return {"books": books, "count": len(books)}


@app.get("/api/discovery/shelves")
def discovery_shelves_endpoint(request: Request):
    """Retorna as prateleiras dinâmicas de descoberta inspiradas em serviços de streaming."""
    user = require_user(request)
    shelves = get_discovery_shelves(user_id=user["id"])
    return {"shelves": shelves, "count": len(shelves)}


@app.get("/api/taxonomy/tree")
def taxonomy_tree_endpoint(request: Request):
    """Retorna a árvore taxonômica estruturada de categorias e facetas presentes no acervo."""
    require_user(request)
    tree = get_taxonomy_tree()
    facets = get_taxonomy_facets()
    return {
        "tree": tree,
        "uncategorized_count": facets["uncategorized_count"],
        "subcategories": facets["subcategories"],
        "genres": facets["genres"],
        "origins": facets["origins"],
    }


@app.get("/api/categories")
def list_categories_endpoint(request: Request, include_empty: bool = True):
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
def create_category_endpoint(payload: CategoryCreateRequest, request: Request):
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
def update_category_endpoint(category_id: int, payload: CategoryUpdateRequest, request: Request):
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
def delete_category_endpoint(category_id: int, request: Request):
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
def merge_categories_endpoint(payload: CategoryMergeRequest, request: Request):
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
def reorder_categories_endpoint(payload: CategoryReorderRequest, request: Request):
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
def set_book_categories_endpoint(book_id: str, payload: BookCategoriesSetRequest, request: Request):
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
def batch_categories_endpoint(payload: BatchCategoryAssignmentRequest, request: Request):
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
def set_reading_status_endpoint(book_id: str, payload: ReadingStatusSetRequest, request: Request):
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
def reclassify_auto_endpoint(request: Request):
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
def retrieve_book(book_id: str, request: Request):
    """Retorna detalhes de um livro específico para o usuário autenticado."""
    user = require_user(request)
    try:
        return get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/books/{book_id}/reader-representation")
def get_reader_representation(
    book_id: str,
    request: Request,
    response: Response,
    representation: Literal["auto", "original"] = Query("auto"),
):
    """
    Resolve e fixa a representação de leitura (original ou derivado otimizado validado)
    para toda a sessão do PDF.js, retornando URL pinada (`?rep=...`) e `content_length` exato.
    """
    user = require_user(request)
    try:
        file_path, stat_result, _ = resolve_book_file_for_user(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    document_optimization_service.mark_reading_activity()
    pinned = document_optimization_service.pin_reader_representation(
        book_id=book_id,
        user_id=user["id"],
        original_path=file_path,
        original_stat=stat_result,
        preferred=representation,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    return pinned


@app.get("/api/books/{book_id}/file")
@app.head("/api/books/{book_id}/file")
def stream_book_file(
    book_id: str,
    request: Request,
    representation: Literal["auto", "original"] = Query("auto"),
    rep: Optional[str] = Query(None),
):
    """
    Disponibiliza o arquivo PDF/EPUB com suporte a HTTP Range requests (206 Partial Content),
    inspeção de cabeçalhos (HEAD), validação condicional com ETag e políticas seguras de cache.
    Quando `?rep=<rep_id>` é fornecido, fixa estritamente a representação da sessão de leitura
    (nunca misturando bytes de original e derivado).
    """
    user = require_user(request)
    try:
        file_path, stat_result, media_type = resolve_book_file_for_user(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    document_optimization_service.mark_reading_activity()

    if rep:
        try:
            served_path, served_stat, served_rep = document_optimization_service.resolve_pinned_representation(
                rep_id=rep,
                book_id=book_id,
                user_id=user["id"],
                original_path=file_path,
                original_stat=stat_result,
            )
        except document_optimization_service.RepresentationForbiddenError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except document_optimization_service.RepresentationStaleError as e:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"code": e.code, "detail": str(e)},
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Document-Representation-Stale": "1",
                },
            )
    else:
        served_path, served_stat, served_rep = (
            document_optimization_service.resolve_pdf_for_reader(book_id, file_path, stat_result)
            if representation == "auto"
            else (file_path, stat_result, "original")
        )

    etag_base = f"{served_rep}-{served_stat.st_mtime_ns}-{served_stat.st_size}-{served_stat.st_ino}"
    etag = f'"{hashlib.sha256(etag_base.encode()).hexdigest()[:24]}"'
    last_modified = formatdate(served_stat.st_mtime, usegmt=True)

    cache_headers = {
        "Cache-Control": "private, no-cache",
        "Vary": "Cookie",
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
        "X-Document-Representation": served_rep,
    }
    if rep:
        cache_headers["X-Document-Representation-Id"] = rep

    # Validação condicional If-None-Match (somente após require_user + resolve_book_file_for_user, sem Range)
    if_none_match = request.headers.get("if-none-match")
    if not request.headers.get("range") and etag_matches_if_none_match(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    return FileResponse(
        path=served_path,
        media_type=media_type,
        filename=file_path.name,
        stat_result=served_stat,
        headers=cache_headers,
    )


@app.get("/api/books/{book_id}/download")
def download_book_file(book_id: str, request: Request):
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
@app.head("/api/books/{book_id}/cover")
def get_book_cover_endpoint(
    book_id: str,
    request: Request,
    variant: Literal["original", "card", "detail"] = Query("original"),
):
    """
    Entrega a capa do livro (original ou thumbnail derivado `card`/`detail`, ou capa editorial SVG).
    Valida autenticação e acesso antes de qualquer resposta condicional 304.
    """
    require_user(request)
    try:
        cover = get_or_create_book_cover(book_id, variant=variant)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    resolved_variant = cover.get("variant") or variant
    file_path = cover.get("file_path")

    if file_path is not None:
        st = file_path.stat()
        etag_base = f"{st.st_mtime_ns}-{st.st_size}-{st.st_ino}-{resolved_variant}"
        etag = f'"{hashlib.sha256(etag_base.encode()).hexdigest()[:24]}"'
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache",
            "Vary": "Cookie",
            "ETag": etag,
            "Last-Modified": formatdate(st.st_mtime, usegmt=True),
            "X-Cover-Type": cover["type"],
            "X-Cover-Variant": resolved_variant,
        }
        if etag_matches_if_none_match(request.headers.get("if-none-match"), etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        if request.method.upper() == "HEAD":
            headers["Content-Length"] = str(st.st_size)
            return Response(content=b"", media_type=cover["media_type"], headers=headers)
        return FileResponse(
            path=file_path,
            media_type=cover["media_type"],
            stat_result=st,
            headers=headers,
        )

    svg_bytes = cover["content"]
    etag = f'"{hashlib.sha256(svg_bytes).hexdigest()[:24]}"'
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-cache",
        "Vary": "Cookie",
        "ETag": etag,
        "X-Cover-Type": cover["type"],
        "X-Cover-Variant": resolved_variant,
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    }
    if etag_matches_if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if request.method.upper() == "HEAD":
        headers["Content-Length"] = str(len(svg_bytes))
        return Response(content=b"", media_type=cover["media_type"], headers=headers)
    return Response(
        content=svg_bytes,
        media_type=cover["media_type"],
        headers=headers,
    )


@app.post("/api/books/{book_id}/cover")
async def upload_custom_book_cover_endpoint(book_id: str, request: Request):
    """
    Define ou substitui uma capa personalizada (PNG, JPEG ou WebP) para um livro.
    """
    await run_in_threadpool(require_user, request)
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        image_bytes = extract_uploaded_image_from_request(body, content_type)
        result = await run_in_threadpool(save_custom_cover, book_id, image_bytes)
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
def remove_custom_book_cover_endpoint(book_id: str, request: Request):
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
    await run_in_threadpool(require_user, request)
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        image_bytes = extract_uploaded_image_from_request(body, content_type)
        result = await run_in_threadpool(save_rendered_cover_cache, book_id, image_bytes)
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
def trigger_scan(request: Request):
    """
    Executa a varredura manual de todas as fontes sob demanda.
    Requer privilégios de administrador.
    """
    require_admin(request)
    stats = scan_library()
    document_optimization_service.notify_scan_completed()
    return {"status": "success", "stats": stats}


# =========================================================================
# ENDPOINTS DE METADADOS BIBLIOGRÁFICOS E RECONCILIAÇÃO EXTERNA (v1.4.0)
# =========================================================================

@app.get("/api/books/{book_id}/metadata/candidates")
def get_book_metadata_candidates_endpoint(book_id: str, request: Request):
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
def search_book_metadata_endpoint(book_id: str, payload: MetadataSearchPayload, request: Request):
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
def apply_book_metadata_candidate_endpoint(book_id: str, payload: MetadataApplyPayload, request: Request):
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
def update_book_metadata_endpoint(book_id: str, payload: MetadataUpdatePayload, request: Request):
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
def metadata_queue_status_endpoint(request: Request):
    """Consulta o status detalhado da fila de enriquecimento em segundo plano."""
    require_admin(request)
    return enricher.get_status()


@app.post("/api/metadata/queue/start")
async def metadata_queue_start_endpoint(request: Request):
    """Inicia ou retoma o processamento assíncrono da fila de enriquecimento."""
    await run_in_threadpool(require_admin, request)
    enricher.start()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/pause")
async def metadata_queue_pause_endpoint(request: Request):
    """Pausa a fila de enriquecimento sem perder a posição atual."""
    await run_in_threadpool(require_admin, request)
    enricher.pause()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/cancel")
async def metadata_queue_cancel_endpoint(request: Request):
    """Cancela e esvazia a fila de enriquecimento."""
    await run_in_threadpool(require_admin, request)
    enricher.cancel()
    return {"status": "success", "queue": enricher.get_status()}


@app.post("/api/metadata/queue/enqueue")
async def metadata_queue_enqueue_endpoint(payload: MetadataQueueEnqueuePayload, request: Request):
    """Enfileira obras para enriquecimento inteligente."""
    await run_in_threadpool(require_admin, request)
    count = await run_in_threadpool(
        enricher.enqueue_books,
        payload.book_ids,
        only_pending=payload.only_pending,
    )
    enricher.start()
    return {"status": "success", "enqueued_count": count, "queue": enricher.get_status()}


# =========================================================================
# ENDPOINTS DE COLEÇÕES E CATEGORIAS (ISOLADOS POR USUÁRIO)
# =========================================================================

@app.get("/api/collections")
def get_collections(request: Request):
    """Lista todas as coleções do usuário autenticado."""
    user = require_user(request)
    return {"collections": list_collections(user_id=user["id"])}


@app.post("/api/collections", status_code=status.HTTP_201_CREATED)
def create_new_collection(payload: CollectionCreatePayload, request: Request):
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
def retrieve_collection(collection_id: int, request: Request):
    """Retorna detalhes de uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        return get_collection(collection_id, user_id=user["id"])
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/collections/{collection_id}")
def modify_collection(collection_id: int, payload: CollectionCreatePayload, request: Request):
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
def remove_collection(collection_id: int, request: Request):
    """Remove uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        delete_collection(collection_id, user_id=user["id"])
        return {"status": "success", "message": "Coleção excluída com sucesso."}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/collections/{collection_id}/books")
def add_book_to_col(collection_id: int, payload: CollectionBookPayload, request: Request):
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
def remove_book_from_col(collection_id: int, book_id: str, request: Request):
    """Remove a associação de um livro com uma coleção do usuário autenticado."""
    user = require_user(request)
    try:
        removed = remove_book_from_collection(collection_id, book_id, user_id=user["id"])
        return {"status": "success", "removed": removed}
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/books/{book_id}/collections")
def update_book_collections_endpoint(book_id: str, payload: BookCollectionsPayload, request: Request):
    """Atualiza as coleções do usuário às quais o livro pertence."""
    user = require_user(request)
    try:
        cols = set_book_collections(book_id, payload.collection_ids, user_id=user["id"])
        return {"status": "success", "collections": cols}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# ENDPOINTS DE ANOTAÇÕES, MARCA-TEXTO, DESENHOS E MARCADORES (v1.8.0)
# =========================================================================

@app.get("/api/books/{book_id}/annotations")
def get_all_book_annotations(
    book_id: str,
    request: Request,
    response: Response,
    scope: Literal["mine", "public", "all_visible"] = Query(default="mine"),
    search: Optional[str] = Query(default=None, max_length=300),
    sort: Literal["position", "recent"] = Query(default="position"),
    epub_cfi: Optional[str] = Query(default=None, max_length=MAX_EPUB_CFI_LEN),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    knowledge_filter: Literal["all", "connected", "unconnected"] = Query(default="all"),
):
    """Retorna o resumo de anotações, destaques, desenhos e marcadores conforme o escopo."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        get_book(book_id, user_id=user["id"])
        summary = get_book_annotations_summary(
            book_id=book_id,
            user_id=user["id"],
            scope=scope,
            epub_cfi=epub_cfi,
            search=search,
            sort_by=sort,
            limit=limit,
            offset=offset,
            knowledge_filter=knowledge_filter,
        )
        return summary
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/books/{book_id}/pages/{page}/annotations")
def get_page_annotations(
    book_id: str,
    page: int,
    request: Request,
    response: Response,
    scope: Literal["mine", "public", "all_visible"] = Query(default="mine"),
    epub_cfi: Optional[str] = Query(default=None, max_length=MAX_EPUB_CFI_LEN),
    search: Optional[str] = Query(default=None, max_length=300),
    sort: Literal["position", "recent"] = Query(default="position"),
):
    """Retorna os destaques, notas e desenhos (próprios e públicos) de uma página/posição."""
    user = require_user(request)
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Número de página inválido.")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        get_book(book_id, user_id=user["id"])
        highlights = get_highlights(
            book_id,
            page=page,
            user_id=user["id"],
            scope=scope,
            epub_cfi=epub_cfi,
            search=search,
            sort_by=sort,
        )
        notes = get_notes(
            book_id,
            page=page,
            user_id=user["id"],
            scope=scope,
            epub_cfi=epub_cfi,
            search=search,
            sort_by=sort,
        )
        bundle = get_page_drawings_bundle(book_id, page=page, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    own_drawing = bundle["own_drawing"]
    public_drawings = bundle["public_drawings"] if scope in ("public", "all_visible") else []

    return {
        "page": page,
        "scope": scope,
        "highlights": highlights,
        "notes": notes,
        "drawing": own_drawing,
        "own_drawing": own_drawing,
        "public_drawings": public_drawings,
    }


@app.post("/api/books/{book_id}/highlights", status_code=status.HTTP_201_CREATED)
def create_new_highlight(book_id: str, payload: HighlightCreatePayload, request: Request):
    """Cria um novo destaque de texto vinculado ao usuário autenticado (privado por padrão)."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
        highlight = create_highlight(
            book_id=book_id,
            page=payload.page,
            color=payload.color,
            rects=payload.rects,
            selected_text=payload.selected_text,
            text_context=payload.text_context,
            user_id=user["id"],
            epub_cfi=payload.epub_cfi,
            chapter_label=payload.chapter_label,
            visibility=payload.visibility,
        )
        return {"status": "success", "highlight": highlight}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.patch("/api/books/{book_id}/highlights/{highlight_id}/visibility")
@app.patch("/api/highlights/{highlight_id}/visibility")
def patch_highlight_visibility(
    highlight_id: str,
    payload: AnnotationVisibilityPayload,
    request: Request,
    book_id: Optional[str] = None,
):
    """Altera explicitamente a visibilidade ('private' | 'public') de um destaque do próprio usuário."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        updated = set_highlight_visibility(
            highlight_id=highlight_id,
            visibility=payload.visibility,
            user_id=user["id"],
            book_id=book_id,
        )
        return {"status": "success", "highlight": updated}
    except (SecurityError, AnnotationPermissionError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except (BookNotFoundError, AnnotationNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/highlights/{highlight_id}")
@app.delete("/api/highlights/{highlight_id}")
def remove_highlight(highlight_id: str, request: Request, book_id: Optional[str] = None):
    """Remove um destaque do usuário autenticado."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        deleted = delete_highlight(highlight_id, user_id=user["id"], book_id=book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destaque não encontrado.")
    return {"status": "success", "message": "Destaque removido com sucesso."}


@app.post("/api/books/{book_id}/notes", status_code=status.HTTP_201_CREATED)
def create_new_note(book_id: str, payload: NoteCreatePayload, request: Request):
    """Cria uma nova nota textual vinculada ao usuário autenticado (privada por padrão)."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
        note = create_note(
            book_id=book_id,
            page=payload.page,
            content=payload.content,
            color=payload.color,
            highlight_id=payload.highlight_id,
            user_id=user["id"],
            epub_cfi=payload.epub_cfi,
            chapter_label=payload.chapter_label,
            visibility=payload.visibility,
        )
        return {"status": "success", "note": note}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/api/books/{book_id}/notes/{note_id}")
@app.put("/api/notes/{note_id}")
def modify_note(note_id: str, payload: NoteUpdatePayload, request: Request, book_id: Optional[str] = None):
    """Atualiza uma nota do usuário autenticado."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        updated = update_note(
            note_id=note_id,
            content=payload.content,
            color=payload.color,
            user_id=user["id"],
            visibility=payload.visibility,
            chapter_label=payload.chapter_label,
            book_id=book_id,
        )
        return {"status": "success", "note": updated}
    except (SecurityError, AnnotationPermissionError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except (BookNotFoundError, AnnotationNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.patch("/api/books/{book_id}/notes/{note_id}/visibility")
@app.patch("/api/notes/{note_id}/visibility")
def patch_note_visibility(
    note_id: str,
    payload: AnnotationVisibilityPayload,
    request: Request,
    book_id: Optional[str] = None,
):
    """Altera explicitamente a visibilidade ('private' | 'public') de uma nota do próprio usuário."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        updated = set_note_visibility(
            note_id=note_id,
            visibility=payload.visibility,
            user_id=user["id"],
            book_id=book_id,
        )
        return {"status": "success", "note": updated}
    except (SecurityError, AnnotationPermissionError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except (BookNotFoundError, AnnotationNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/notes/{note_id}")
@app.delete("/api/notes/{note_id}")
def remove_note(note_id: str, request: Request, book_id: Optional[str] = None):
    """Exclui uma nota do usuário autenticado."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        deleted = delete_note(note_id, user_id=user["id"], book_id=book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nota não encontrada.")
    return {"status": "success", "message": "Nota removida com sucesso."}


@app.get("/api/books/{book_id}/pages/{page}/drawing")
def get_drawing_endpoint(
    book_id: str,
    page: int,
    request: Request,
    response: Response,
    scope: Literal["mine", "public", "all_visible"] = Query(default="all_visible"),
):
    """Recupera os traços vetoriais da página (próprios e públicos de outros leitores)."""
    user = require_user(request)
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Número de página inválido.")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        get_book(book_id, user_id=user["id"])
        bundle = get_page_drawings_bundle(book_id=book_id, page=page, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    own = bundle["own_drawing"]
    pub = bundle["public_drawings"] if scope in ("public", "all_visible") else []
    return {
        **own,
        "own_drawing": own,
        "public_drawings": pub,
    }


@app.put("/api/books/{book_id}/pages/{page}/drawing")
def update_page_drawing(book_id: str, page: int, payload: DrawingSavePayload, request: Request):
    """Salva os traços vetoriais da página para o usuário autenticado."""
    user = require_user(request)
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Número de página inválido.")
    try:
        get_book(book_id, user_id=user["id"])
        result = save_page_drawing(
            book_id=book_id,
            page=page,
            strokes=payload.strokes,
            user_id=user["id"],
            visibility=payload.visibility,
        )
        return result
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.patch("/api/books/{book_id}/pages/{page}/drawing/visibility")
def patch_page_drawing_visibility(
    book_id: str,
    page: int,
    payload: AnnotationVisibilityPayload,
    request: Request,
):
    """Altera explicitamente a visibilidade ('private' | 'public') do desenho da página do usuário."""
    user = require_user(request)
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Número de página inválido.")
    try:
        get_book(book_id, user_id=user["id"])
        updated = set_page_drawing_visibility(
            book_id=book_id,
            page=page,
            visibility=payload.visibility,
            user_id=user["id"],
        )
        return {"status": "success", "drawing": updated}
    except (SecurityError, AnnotationPermissionError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except (BookNotFoundError, AnnotationNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/pages/{page}/drawing")
def clear_page_drawing(book_id: str, page: int, request: Request):
    """Limpa todos os traços de desenho da página para o usuário autenticado."""
    user = require_user(request)
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Número de página inválido.")
    try:
        get_book(book_id, user_id=user["id"])
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    deleted = delete_page_drawing(book_id=book_id, page=page, user_id=user["id"])
    return {"status": "success", "cleared": deleted}


@app.get("/api/books/{book_id}/bookmarks")
def get_book_bookmarks_endpoint(
    book_id: str,
    request: Request,
    response: Response,
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Lista os marcadores pessoais (sempre privados) do usuário autenticado para o livro."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        get_book(book_id, user_id=user["id"])
        bookmarks = list_bookmarks(book_id=book_id, user_id=user["id"], limit=limit, offset=offset)
        return {"book_id": book_id, "bookmarks": bookmarks, "count": len(bookmarks)}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/books/{book_id}/bookmarks", status_code=status.HTTP_201_CREATED)
def create_book_bookmark_endpoint(
    book_id: str,
    payload: BookmarkCreatePayload,
    request: Request,
):
    """Cria ou atualiza um marcador pessoal de leitura para página PDF ou posição EPUB CFI."""
    user = require_user(request)
    try:
        get_book(book_id, user_id=user["id"])
        bookmark = create_bookmark(
            book_id=book_id,
            user_id=user["id"],
            page=payload.page,
            epub_cfi=payload.epub_cfi,
            chapter_label=payload.chapter_label,
            label=payload.label or "",
        )
        return {"status": "success", "bookmark": bookmark}
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/api/books/{book_id}/bookmarks/{bookmark_id}")
@app.delete("/api/bookmarks/{bookmark_id}")
def delete_book_bookmark_endpoint(
    bookmark_id: str,
    request: Request,
    book_id: Optional[str] = None,
):
    """Remove um marcador pessoal pertencente ao usuário autenticado."""
    user = require_user(request)
    try:
        if book_id is not None:
            get_book(book_id, user_id=user["id"])
        deleted = delete_bookmark(bookmark_id=bookmark_id, user_id=user["id"], book_id=book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marcador não encontrado.")
    return {"status": "success", "message": "Marcador removido com sucesso."}


# =========================================================================
# ENDPOINTS DE PROGRESSO DE LEITURA
# =========================================================================

@app.get("/api/books/{book_id}/progress")
def read_progress(book_id: str, request: Request):
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
    user = await run_in_threadpool(require_user, request)
    try:
        book = await run_in_threadpool(get_book, book_id, user_id=user["id"])
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

    def _persist_progress_sync():
        updated_res = save_progress(
            book_id=book_id,
            current_page=current_page,
            total_pages=total_pages,
            user_id=user["id"],
            progress_locator=progress_locator,
        )
        social_service.record_reading_milestone(
            user_id=user["id"],
            book_id=book_id,
            percentage=updated_res.get("percentage", 0.0),
            current_page=current_page,
        )
        return updated_res

    updated = await run_in_threadpool(_persist_progress_sync)
    return {"status": "success", "progress": updated}


# =========================================================================
# EXPORTAÇÃO E IMPORTAÇÃO DE ESTUDOS
# =========================================================================

@app.get("/api/books/{book_id}/export/markdown")
def export_annotations_markdown(book_id: str, request: Request):
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
def export_annotations_json(book_id: str, request: Request):
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
    user = await run_in_threadpool(require_user, request)
    try:
        payload = await _parse_import_body(request)
        preview = await run_in_threadpool(preview_import_annotations, book_id, payload, user_id=user["id"])
        return {"status": "success", "preview": preview}
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/books/{book_id}/import/confirm")
async def confirm_annotations_import(book_id: str, request: Request):
    """Aplica a importação de anotações dentro de uma transação SQLite protegida para o usuário."""
    user = await run_in_threadpool(require_user, request)
    try:
        payload = await _parse_import_body(request)
        result = await run_in_threadpool(apply_import_annotations, book_id, payload, user_id=user["id"])
        return {"status": "success", "result": result}
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =========================================================================
# BACKUPS MANUAIS DO BANCO SQLITE (ADMINISTRADOR APENAS)
# =========================================================================

@app.post("/api/backup")
def trigger_manual_backup(request: Request):
    """Gera uma cópia de segurança pontual do banco SQLite em backups/."""
    require_admin(request)
    try:
        backup_info = create_backup()
        return {"status": "success", "backup": backup_info}
    except BackupError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/api/backups")
def get_available_backups(request: Request):
    """Lista todos os backups manuais disponíveis no servidor."""
    require_admin(request)
    backups = list_backups()
    return {"status": "success", "backups": backups}


# =========================================================================
# ENDPOINTS DE REVISÃO BIBLIOGRÁFICA E DIAGNÓSTICO DE METADADOS (v1.5.0)
# =========================================================================

@app.get("/api/metadata/diagnostics")
def metadata_diagnostics_endpoint(request: Request):
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
def mark_book_metadata_reviewed_endpoint(
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
def reject_book_metadata_candidates_endpoint(book_id: str, request: Request):
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
def reprocess_single_book_metadata_endpoint(book_id: str, request: Request):
    """Reprocessa imediatamente a identificação bibliográfica de um livro específico."""
    user = require_admin(request)
    try:
        get_book(book_id, user_id=user["id"])
    except (SecurityError, BookNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    enricher._enrich_single_book_sync(book_id)
    return {"status": "success", "book": get_book(book_id, user_id=user["id"])}


@app.post("/api/metadata/batch-update")
def batch_update_metadata_endpoint(payload: BatchMetadataUpdatePayload, request: Request):
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
def list_automation_schedules_endpoint(request: Request):
    """Lista todas as rotinas agendadas configuráveis (Admin)."""
    require_admin(request)
    return {"schedules": automation_service.list_schedules()}


@app.put("/api/automation/schedules/{job_type}")
def update_automation_schedule_endpoint(
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
def list_automation_jobs_endpoint(
    request: Request,
    limit: int = 50,
    status: Optional[str] = None,
):
    """Lista o histórico persistente de execuções de tarefas em segundo plano (Admin)."""
    require_admin(request)
    return {"jobs": automation_service.list_jobs(limit=limit, status_filter=status)}


@app.post("/api/automation/jobs/run")
def run_automation_job_endpoint(payload: JobRunPayload, request: Request):
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
def cancel_automation_job_endpoint(job_id: int, request: Request):
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
def admin_dashboard_endpoint(request: Request):
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
def admin_library_diagnostics_endpoint(request: Request):
    """Retorna diagnósticos detalhados de arquivos ausentes, duplicatas, metadados incompletos e formatos não reconhecidos."""
    require_admin(request)
    return get_library_diagnostics()


@app.post("/api/users/{user_id}/revoke-sessions")
def revoke_user_sessions_endpoint(user_id: int, request: Request):
    """Encerra todas as sessões ativas de um usuário específico (Admin)."""
    admin = require_admin(request)
    count = revoke_user_sessions(user_id)
    log_admin_action(admin["id"], "revoke_user_sessions", f"user_id={user_id}", f"{count} sessões encerradas")
    return {"status": "success", "revoked_sessions": count}


@app.get("/api/admin/audit-logs")
def admin_audit_logs_endpoint(request: Request, limit: int = 100):
    """Retorna os logs de auditoria administrativa."""
    require_admin(request)
    return {"logs": list_admin_audit_logs(limit=limit)}


@app.get("/api/admin/maintenance/status")
def admin_maintenance_status_endpoint(request: Request):
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
def admin_clear_cache_endpoint(payload: ClearCachePayload, request: Request):
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
def get_my_profile_endpoint(request: Request):
    """Retorna o perfil completo, estatísticas e configurações de privacidade do usuário logado."""
    user = require_user(request)
    prof = social_service.get_user_profile(user["id"], viewer_user_id=user["id"])
    return {"profile": prof, **prof}


@app.put("/api/profile/me")
def update_my_profile_endpoint(payload: ProfileUpdatePayload, request: Request):
    """Atualiza nome de exibição, cor de avatar, bio e preferências de leitura do usuário logado."""
    user = require_user(request)
    updated = social_service.update_user_profile(user["id"], payload.model_dump(exclude_none=True))
    return {"status": "success", "profile": updated, **updated}


@app.get("/api/profile/privacy")
@app.get("/api/profile/me/privacy")
def get_my_privacy_endpoint(request: Request):
    """Retorna as configurações de privacidade do usuário logado (privado por padrão)."""
    user = require_user(request)
    priv = social_service.get_privacy_settings(user["id"])
    return {"privacy": priv, **priv}


@app.put("/api/profile/privacy")
@app.put("/api/profile/me/privacy")
def update_my_privacy_endpoint(payload: PrivacyUpdatePayload, request: Request):
    """Atualiza as permissões de compartilhamento do usuário logado."""
    user = require_user(request)
    updated = social_service.update_privacy_settings(user["id"], payload.model_dump(exclude_none=True))
    return {"status": "success", "privacy": updated, **updated}


@app.get("/api/users/{target_user_id}/profile")
def get_other_user_profile_endpoint(target_user_id: int, request: Request):
    """Retorna o perfil de outro usuário filtrando estritamente pelos controles de privacidade dele."""
    user = require_user(request)
    try:
        prof = social_service.get_user_profile(target_user_id, viewer_user_id=user["id"])
        return {"profile": prof, **prof}
    except SocialNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/social/users")
def list_social_users_endpoint(request: Request):
    """Lista os membros ativos da biblioteca para seleção em recomendações comunitárias."""
    user = require_user(request)
    feed = social_service.get_nossa_biblioteca_feed(viewer_user_id=user["id"])
    return {"users": feed.get("members", []), "current_user_id": user["id"]}


@app.get("/api/books/{book_id}/reviews")
def get_book_reviews_endpoint(book_id: str, request: Request):
    """Retorna avaliações (1 a 5 estrelas) e resenhas compartilhadas de um livro."""
    user = require_user(request)
    return social_service.get_book_reviews_summary(book_id, viewer_user_id=user["id"])


@app.post("/api/books/{book_id}/reviews")
@app.put("/api/books/{book_id}/reviews")
@app.put("/api/books/{book_id}/reviews/me")
def upsert_book_review_endpoint(book_id: str, payload: BookReviewPayload, request: Request):
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
def list_book_shared_annotations_endpoint(book_id: str, request: Request):
    """Lista todas as anotações e citações compartilhadas de um livro."""
    user = require_user(request)
    items = social_service.list_shared_annotations(viewer_user_id=user["id"], book_id=book_id)
    return {"annotations": items, "count": len(items)}


@app.get("/api/shared-annotations")
@app.get("/api/social/annotations")
def list_all_shared_annotations_endpoint(
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
def create_shared_annotation_endpoint(
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
def update_shared_annotation_endpoint(
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
def delete_shared_annotation_endpoint(shared_id: int, request: Request):
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
def add_comment_to_shared_annotation_endpoint(
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
def edit_annotation_comment_endpoint(
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
def delete_annotation_comment_endpoint(comment_id: int, request: Request):
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
def list_recommendations_endpoint(request: Request):
    """Lista recomendações de leitura recebidas e enviadas pelo usuário."""
    user = require_user(request)
    return social_service.list_user_recommendations(user["id"])


@app.post("/api/recommendations", status_code=status.HTTP_201_CREATED)
@app.post("/api/social/recommendations", status_code=status.HTTP_201_CREATED)
def create_recommendation_endpoint(
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
def get_nossa_biblioteca_feed_endpoint(request: Request):
    """Retorna o mural consolidado 'Nossa Biblioteca' respeitando as permissões de privacidade."""
    user = require_user(request)
    return social_service.get_nossa_biblioteca_feed(viewer_user_id=user["id"])


# =========================================================================
# ENDPOINTS REST: CLUBES DE LEITURA E LEITURA COLETIVA (v1.6.0)
# =========================================================================

@app.get("/api/reading-clubs")
def list_reading_clubs_endpoint(
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
def create_reading_club_endpoint(payload: ClubCreatePayload, request: Request):
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
def list_my_invites_endpoint(request: Request, status_filter: str = "pending"):
    """Lista convites recebidos pelo usuário autenticado (estática antes de /{club_id})."""
    user = require_user(request)
    invites = clubs_service.list_user_invites(user_id=user["id"], status=status_filter)
    return {"invites": invites, "count": len(invites)}


@app.post("/api/reading-clubs/invites/{invite_id}/respond")
def respond_invite_endpoint(invite_id: str, payload: InviteResponsePayload, request: Request):
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
def cancel_invite_endpoint(invite_id: str, request: Request):
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
def get_reading_club_endpoint(club_id: str, request: Request):
    """Retorna dados completos de um clube de leitura (anti-enumeração para clubes privados)."""
    user = require_user(request)
    try:
        club = clubs_service.get_club(club_id=club_id, user_id=user["id"])
        return {"club": club}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/reading-clubs/{club_id}")
def update_reading_club_endpoint(club_id: str, payload: ClubUpdatePayload, request: Request):
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
def archive_reading_club_endpoint(club_id: str, request: Request, payload: Optional[ClubArchivePayload] = None):
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
def join_reading_club_endpoint(club_id: str, request: Request):
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
def leave_reading_club_endpoint(club_id: str, request: Request):
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
def list_club_members_endpoint(club_id: str, request: Request):
    """Lista todos os participantes de um clube e seus papéis."""
    user = require_user(request)
    try:
        members = clubs_service.list_club_members(club_id=club_id, user_id=user["id"])
        return {"members": members, "count": len(members)}
    except ClubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/reading-clubs/{club_id}/members/{target_user_id}")
def update_club_member_role_endpoint(
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
def remove_club_member_endpoint(club_id: str, target_user_id: int, request: Request):
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
def transfer_club_ownership_endpoint(
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
def create_club_invite_endpoint(
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
def list_club_invites_endpoint(club_id: str, request: Request):
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
def list_club_reads_endpoint(club_id: str, request: Request, status_filter: Optional[str] = None):
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
def create_collective_read_endpoint(
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
def list_my_collective_reads_endpoint(request: Request, status_filter: Optional[str] = None):
    """Lista todas as leituras coletivas nas quais o usuário autenticado participa."""
    user = require_user(request)
    reads = clubs_service.list_user_collective_reads(user_id=user["id"], status=status_filter)
    return {"reads": reads, "count": len(reads)}


@app.get("/api/collective-reads/{read_id}")
def get_collective_read_endpoint(read_id: str, request: Request):
    """Retorna detalhes de uma leitura coletiva, metadados do livro e participantes."""
    user = require_user(request)
    try:
        read = clubs_service.get_collective_read(read_id=read_id, user_id=user["id"])
        return {"read": read}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.patch("/api/collective-reads/{read_id}")
def update_collective_read_endpoint(
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
def join_collective_read_endpoint(read_id: str, request: Request):
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
def leave_collective_read_endpoint(read_id: str, request: Request):
    """Permite a saída de um participante da leitura coletiva."""
    user = require_user(request)
    try:
        res = clubs_service.leave_collective_read(read_id=read_id, user_id=user["id"])
        return {"status": "success", "result": res}
    except CollectiveReadNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# ENDPOINTS REST: LEITURA COMPARTILHADA (v1.7.0)
# =========================================================================

def _handle_shared_reading_error(exc: Exception) -> HTTPException:
    """Padroniza o mapeamento das exceções de Leitura Compartilhada para HTTP."""
    if isinstance(
        exc,
        (
            CollectiveReadNotFoundError,
            TopicNotFoundError,
            PostNotFoundError,
            GoalNotFoundError,
            QuoteNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ClubPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, InvalidOperationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Erro interno ao processar operação de leitura compartilhada.",
    )


@app.get("/api/collective-reads/{read_id}/overview")
def get_shared_reading_overview_endpoint(read_id: str, request: Request):
    """Retorna visão geral da leitura coletiva (contadores, discussões recentes sem spoiler e metas)."""
    user = require_user(request)
    try:
        return shared_reading_service.get_shared_reading_overview(read_id=read_id, user_id=user["id"])
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.get("/api/collective-reads/{read_id}/topics")
def list_shared_topics_endpoint(
    read_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=10000),
    limit: int = Query(default=20, ge=1, le=50),
    filter: Literal["all", "pinned", "spoilers"] = Query(default="all"),
):
    """Lista tópicos paginados da leitura coletiva com mascaramento de spoilers."""
    user = require_user(request)
    try:
        return shared_reading_service.list_topics(
            read_id=read_id,
            user_id=user["id"],
            page=page,
            limit=limit,
            filter_mode=filter,
        )
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.post("/api/collective-reads/{read_id}/topics", status_code=status.HTTP_201_CREATED)
def create_shared_topic_endpoint(
    read_id: str,
    payload: SharedTopicCreatePayload,
    request: Request,
):
    """Cria um novo tópico de discussão na leitura coletiva."""
    user = require_user(request)
    try:
        topic = shared_reading_service.create_topic(
            read_id=read_id,
            user_id=user["id"],
            title=payload.title,
            content=payload.content,
            chapter_ref=payload.chapter_ref,
            page_start=payload.page_start,
            page_end=payload.page_end,
            epub_cfi=payload.epub_cfi,
            is_spoiler=payload.is_spoiler,
        )
        return {"status": "success", "topic": topic}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.get("/api/collective-reads/{read_id}/topics/{topic_id}")
def get_shared_topic_detail_endpoint(
    read_id: str,
    topic_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=10000),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Retorna os detalhes do tópico (revelação intencional) e suas respostas paginadas."""
    user = require_user(request)
    try:
        return shared_reading_service.get_topic_detail(
            read_id=read_id,
            topic_id=topic_id,
            user_id=user["id"],
            page=page,
            limit=limit,
        )
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.put("/api/collective-reads/{read_id}/topics/{topic_id}")
def edit_shared_topic_endpoint(
    read_id: str,
    topic_id: str,
    payload: SharedTopicUpdatePayload,
    request: Request,
):
    """Edita conteúdo/metadados de um tópico existente (somente autor)."""
    user = require_user(request)
    try:
        topic = shared_reading_service.edit_topic(
            read_id=read_id,
            topic_id=topic_id,
            user_id=user["id"],
            title=payload.title,
            content=payload.content,
            chapter_ref=payload.chapter_ref,
            page_start=payload.page_start,
            page_end=payload.page_end,
            epub_cfi=payload.epub_cfi,
            is_spoiler=payload.is_spoiler,
        )
        return {"status": "success", "topic": topic}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.patch("/api/collective-reads/{read_id}/topics/{topic_id}/moderate")
def moderate_shared_topic_endpoint(
    read_id: str,
    topic_id: str,
    payload: SharedTopicModeratePayload,
    request: Request,
):
    """Fixa ou tranca um tópico (exclusivo para owner/moderator)."""
    user = require_user(request)
    try:
        topic = shared_reading_service.moderate_topic(
            read_id=read_id,
            topic_id=topic_id,
            user_id=user["id"],
            is_pinned=payload.is_pinned,
            is_locked=payload.is_locked,
        )
        return {"status": "success", "topic": topic}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.delete("/api/collective-reads/{read_id}/topics/{topic_id}")
def delete_shared_topic_endpoint(
    read_id: str,
    topic_id: str,
    request: Request,
):
    """Exclusão lógica de tópico (owner/moderator ou autor), preservando respostas."""
    user = require_user(request)
    try:
        topic = shared_reading_service.moderate_topic(
            read_id=read_id,
            topic_id=topic_id,
            user_id=user["id"],
            is_deleted=True,
        )
        return {"status": "success", "topic": topic}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.post("/api/collective-reads/{read_id}/topics/{topic_id}/posts", status_code=status.HTTP_201_CREATED)
def create_shared_post_endpoint(
    read_id: str,
    topic_id: str,
    payload: SharedPostCreatePayload,
    request: Request,
):
    """Cria uma resposta em um tópico de discussão (profundidade máxima 1)."""
    user = require_user(request)
    try:
        post = shared_reading_service.create_post(
            read_id=read_id,
            topic_id=topic_id,
            user_id=user["id"],
            content=payload.content,
            parent_post_id=payload.parent_post_id,
            is_spoiler=payload.is_spoiler,
        )
        return {"status": "success", "post": post}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.put("/api/collective-reads/{read_id}/topics/{topic_id}/posts/{post_id}")
def edit_shared_post_endpoint(
    read_id: str,
    topic_id: str,
    post_id: str,
    payload: SharedPostUpdatePayload,
    request: Request,
):
    """Edita uma resposta própria em um tópico."""
    user = require_user(request)
    try:
        post = shared_reading_service.edit_post(
            read_id=read_id,
            topic_id=topic_id,
            post_id=post_id,
            user_id=user["id"],
            content=payload.content,
            is_spoiler=payload.is_spoiler,
        )
        return {"status": "success", "post": post}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.delete("/api/collective-reads/{read_id}/topics/{topic_id}/posts/{post_id}")
def delete_shared_post_endpoint(
    read_id: str,
    topic_id: str,
    post_id: str,
    request: Request,
):
    """Exclusão lógica de resposta (autor ou owner/moderator)."""
    user = require_user(request)
    try:
        res = shared_reading_service.delete_post(
            read_id=read_id,
            topic_id=topic_id,
            post_id=post_id,
            user_id=user["id"],
        )
        return {"status": "success", "result": res}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.get("/api/collective-reads/{read_id}/goals")
def list_shared_goals_endpoint(
    read_id: str,
    request: Request,
    include_archived: bool = False,
):
    """Lista metas coletivas da leitura e o progresso voluntário agregado."""
    user = require_user(request)
    try:
        return shared_reading_service.list_goals(
            read_id=read_id,
            user_id=user["id"],
            include_archived=include_archived,
        )
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.post("/api/collective-reads/{read_id}/goals", status_code=status.HTTP_201_CREATED)
def create_shared_goal_endpoint(
    read_id: str,
    payload: SharedGoalCreatePayload,
    request: Request,
):
    """Cria uma meta coletiva (exclusivo para owner/moderator)."""
    user = require_user(request)
    try:
        goal = shared_reading_service.create_goal(
            read_id=read_id,
            user_id=user["id"],
            title=payload.title,
            description=payload.description,
            target_date=payload.target_date,
            chapter_ref=payload.chapter_ref,
            target_page=payload.target_page,
            display_order=payload.display_order,
        )
        return {"status": "success", "goal": goal}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.put("/api/collective-reads/{read_id}/goals/{goal_id}")
def update_shared_goal_endpoint(
    read_id: str,
    goal_id: str,
    payload: SharedGoalUpdatePayload,
    request: Request,
):
    """Atualiza, reordena ou arquiva uma meta coletiva (exclusivo para owner/moderator)."""
    user = require_user(request)
    try:
        goal = shared_reading_service.update_goal(
            read_id=read_id,
            goal_id=goal_id,
            user_id=user["id"],
            title=payload.title,
            description=payload.description,
            target_date=payload.target_date,
            chapter_ref=payload.chapter_ref,
            target_page=payload.target_page,
            display_order=payload.display_order,
            is_archived=payload.is_archived,
        )
        return {"status": "success", "goal": goal}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.delete("/api/collective-reads/{read_id}/goals/{goal_id}")
def delete_shared_goal_endpoint(
    read_id: str,
    goal_id: str,
    request: Request,
):
    """Arquiva uma meta coletiva preservando seu histórico (exclusivo para owner/moderator)."""
    user = require_user(request)
    try:
        goal = shared_reading_service.update_goal(
            read_id=read_id,
            goal_id=goal_id,
            user_id=user["id"],
            is_archived=True,
        )
        return {"status": "success", "goal": goal}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.put("/api/collective-reads/{read_id}/goals/{goal_id}/progress")
def update_shared_goal_progress_endpoint(
    read_id: str,
    goal_id: str,
    payload: SharedGoalProgressPayload,
    request: Request,
):
    """Atualiza voluntariamente o progresso individual do usuário em uma meta."""
    user = require_user(request)
    try:
        progress = shared_reading_service.update_voluntary_goal_progress(
            read_id=read_id,
            goal_id=goal_id,
            user_id=user["id"],
            status=payload.status,
        )
        return {"status": "success", "progress": progress}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.get("/api/collective-reads/{read_id}/quotes")
def list_shared_quotes_endpoint(
    read_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=10000),
    limit: int = Query(default=20, ge=1, le=50),
):
    """Lista citações compartilhadas da leitura coletiva com paginação."""
    user = require_user(request)
    try:
        return shared_reading_service.list_quotes(
            read_id=read_id,
            user_id=user["id"],
            page=page,
            limit=limit,
        )
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.post("/api/collective-reads/{read_id}/quotes", status_code=status.HTTP_201_CREATED)
def share_quote_endpoint(
    read_id: str,
    payload: SharedQuoteCreatePayload,
    request: Request,
):
    """Compartilha voluntariamente uma citação manual ou oriunda de nota privada própria."""
    user = require_user(request)
    try:
        quote = shared_reading_service.share_quote(
            read_id=read_id,
            user_id=user["id"],
            quote_text=payload.quote_text,
            comment_text=payload.comment_text,
            chapter_name=payload.chapter_name,
            page_number=payload.page_number,
            epub_cfi=payload.epub_cfi,
            is_spoiler=payload.is_spoiler,
            note_id=payload.note_id,
        )
        return {"status": "success", "quote": quote}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)


@app.delete("/api/collective-reads/{read_id}/quotes/{quote_id}")
def delete_shared_quote_endpoint(
    read_id: str,
    quote_id: str,
    request: Request,
):
    """Exclusão lógica de citação compartilhada (autor ou owner/moderator)."""
    user = require_user(request)
    try:
        res = shared_reading_service.delete_quote(
            read_id=read_id,
            quote_id=quote_id,
            user_id=user["id"],
        )
        return {"status": "success", "result": res}
    except ClubError as exc:
        raise _handle_shared_reading_error(exc)



@app.get("/api/books/{book_id}/collective-reads")
def list_publishable_book_collective_reads_endpoint(book_id: str, request: Request):
    """Lista leituras coletivas elegíveis para publicação de citações pelo usuário nesta obra."""
    user = require_user(request)
    reads = clubs_service.get_publishable_collective_reads_for_book(book_id=book_id, user_id=user["id"])
    return {"reads": reads, "count": len(reads)}


# =========================================================================
# ENDPOINTS REST: CONHECIMENTO CONECTADO / SEÇÃO ATLAS (v1.9.0 — Fase 2)
# =========================================================================

def _handle_knowledge_error(exc: Exception) -> HTTPException:
    """
    Padroniza o mapeamento das exceções de domínio do Conhecimento Conectado (Atlas)
    para códigos HTTP sem vazar mensagens internas do SQLite, stack traces ou caminhos locais.
    """
    if isinstance(exc, KnowledgeNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, KnowledgePermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, KnowledgeConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, KnowledgeExportBusyError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (KnowledgeValidationError, KnowledgeExportLimitError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Erro interno ao processar operação de conhecimento conectado.",
    )


# -------------------------------------------------------------------------
# 1. API DE CONCEITOS (`/api/knowledge/concepts`)
# -------------------------------------------------------------------------

@app.get("/api/knowledge/concepts", response_model=ConceptListResponseModel)
def list_knowledge_concepts_endpoint(
    request: Request,
    response: Response,
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_QUERY_LEN),
    tag: Optional[str] = Query(default=None, max_length=MAX_TAG_LEN),
    include_archived: bool = Query(default=False),
    sort: Literal["updated_desc", "created_desc", "name_asc"] = Query(default="updated_desc"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """Lista conceitos privados pertencentes exclusivamente ao usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.list_concepts(
            user_id=user["id"],
            search=search,
            tag=tag,
            include_archived=include_archived,
            sort_by=sort,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.post("/api/knowledge/concepts", status_code=status.HTTP_201_CREATED)
def create_knowledge_concept_endpoint(
    payload: ConceptCreatePayload,
    request: Request,
    response: Response,
):
    """Cria um novo conceito privado para o usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        concept = knowledge_service.create_concept(
            user_id=user["id"],
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            color=payload.color,
            is_archived=payload.is_archived,
        )
        return {"status": "success", "concept": ConceptResponseModel.model_validate(concept).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get("/api/knowledge/concepts/{concept_id}")
def get_knowledge_concept_endpoint(
    concept_id: str,
    request: Request,
    response: Response,
):
    """Consulta um conceito privado pertencente ao usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        concept = knowledge_service.get_concept(concept_id=concept_id, user_id=user["id"])
        return {"concept": ConceptResponseModel.model_validate(concept).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.patch("/api/knowledge/concepts/{concept_id}")
def update_knowledge_concept_endpoint(
    concept_id: str,
    payload: ConceptUpdatePayload,
    request: Request,
    response: Response,
):
    """Atualiza parcialmente um conceito privado do usuário autenticado preservando seu UUID."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        concept = knowledge_service.update_concept(
            concept_id=concept_id,
            user_id=user["id"],
            name=payload.name if "name" in payload.model_fields_set else None,
            description=payload.description if "description" in payload.model_fields_set else None,
            tags=payload.tags if "tags" in payload.model_fields_set else None,
            color=payload.color if "color" in payload.model_fields_set else None,
            is_archived=payload.is_archived if "is_archived" in payload.model_fields_set else None,
        )
        return {"status": "success", "concept": ConceptResponseModel.model_validate(concept).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.delete("/api/knowledge/concepts/{concept_id}")
def delete_knowledge_concept_endpoint(
    concept_id: str,
    request: Request,
    response: Response,
):
    """Exclui um conceito privado pertencente ao usuário autenticado e limpa seus vínculos."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        res = knowledge_service.delete_concept(concept_id=concept_id, user_id=user["id"])
        return {"status": "success", **res}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 2. API DE REFLEXÕES AUTORAIS (`/api/knowledge/reflections`)
# -------------------------------------------------------------------------

@app.get("/api/knowledge/reflections", response_model=ReflectionListResponseModel)
def list_knowledge_reflections_endpoint(
    request: Request,
    response: Response,
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_QUERY_LEN),
    tag: Optional[str] = Query(default=None, max_length=MAX_TAG_LEN),
    primary_book_id: Optional[str] = Query(default=None, max_length=128),
    include_archived: bool = Query(default=False),
    sort: Literal["updated_desc", "created_desc", "title_asc"] = Query(default="updated_desc"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """Lista reflexões autorais privadas do usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.list_reflections(
            user_id=user["id"],
            search=search,
            tag=tag,
            primary_book_id=primary_book_id,
            include_archived=include_archived,
            sort_by=sort,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.post("/api/knowledge/reflections", status_code=status.HTTP_201_CREATED)
def create_knowledge_reflection_endpoint(
    payload: ReflectionCreatePayload,
    request: Request,
    response: Response,
):
    """Cria uma nova reflexão autoral privada (corpo Markdown armazenado e retornado como texto puro não confiável)."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        reflection = knowledge_service.create_reflection(
            user_id=user["id"],
            title=payload.title,
            body_markdown=payload.body_markdown,
            primary_book_id=payload.primary_book_id,
            tags=payload.tags,
            is_pinned=payload.is_pinned,
            is_archived=payload.is_archived,
        )
        return {"status": "success", "reflection": ReflectionResponseModel.model_validate(reflection).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get("/api/knowledge/reflections/{reflection_id}")
def get_knowledge_reflection_endpoint(
    reflection_id: str,
    request: Request,
    response: Response,
):
    """Consulta uma reflexão autoral privada do usuário autenticado, incluindo referências e backlinks resolvidos."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        reflection = knowledge_service.get_reflection(reflection_id=reflection_id, user_id=user["id"])
        references = knowledge_service.get_reflection_references(reflection_id=reflection_id, user_id=user["id"])
        backlinks = knowledge_service.get_reflection_backlinks(reflection_id=reflection_id, user_id=user["id"])
        return {
            "reflection": ReflectionResponseModel.model_validate(reflection).model_dump(),
            "references": [KnowledgeLinkResponseModel.model_validate(r).model_dump() for r in references],
            "backlinks": [KnowledgeLinkResponseModel.model_validate(b).model_dump() for b in backlinks],
        }
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.patch("/api/knowledge/reflections/{reflection_id}")
def update_knowledge_reflection_endpoint(
    reflection_id: str,
    payload: ReflectionUpdatePayload,
    request: Request,
    response: Response,
):
    """Atualiza parcialmente uma reflexão autoral privada do usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        primary_book_arg: Any = (
            payload.primary_book_id
            if "primary_book_id" in payload.model_fields_set
            else ...
        )
        reflection = knowledge_service.update_reflection(
            reflection_id=reflection_id,
            user_id=user["id"],
            title=payload.title if "title" in payload.model_fields_set else None,
            body_markdown=payload.body_markdown if "body_markdown" in payload.model_fields_set else None,
            primary_book_id=primary_book_arg,
            tags=payload.tags if "tags" in payload.model_fields_set else None,
            is_pinned=payload.is_pinned if "is_pinned" in payload.model_fields_set else None,
            is_archived=payload.is_archived if "is_archived" in payload.model_fields_set else None,
            expected_updated_at=payload.expected_updated_at if "expected_updated_at" in payload.model_fields_set else None,
        )
        return {"status": "success", "reflection": ReflectionResponseModel.model_validate(reflection).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.delete("/api/knowledge/reflections/{reflection_id}")
def delete_knowledge_reflection_endpoint(
    reflection_id: str,
    request: Request,
    response: Response,
):
    """Exclui uma reflexão autoral privada do usuário autenticado e limpa seus vínculos."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        res = knowledge_service.delete_reflection(reflection_id=reflection_id, user_id=user["id"])
        return {"status": "success", **res}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 3. API DE CONEXÕES SEMÂNTICAS (`/api/knowledge/links`)
# -------------------------------------------------------------------------

@app.get("/api/knowledge/links", response_model=KnowledgeLinkListResponseModel)
def list_knowledge_links_endpoint(
    request: Request,
    response: Response,
    source_type: Optional[KnowledgeEntityTypeLiteral] = Query(default=None),
    source_id: Optional[str] = Query(default=None, max_length=128),
    target_type: Optional[KnowledgeEntityTypeLiteral] = Query(default=None),
    target_id: Optional[str] = Query(default=None, max_length=128),
    entity_type: Optional[KnowledgeEntityTypeLiteral] = Query(default=None),
    entity_id: Optional[str] = Query(default=None, max_length=128),
    direction: KnowledgeDirectionLiteral = Query(default="both"),
    relation_type: Optional[KnowledgeRelationTypeLiteral] = Query(default=None),
    sort: Literal["created_desc", "updated_desc"] = Query(default="created_desc"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """Lista conexões semânticas pessoais do usuário autenticado com hidratação segura dos extremos."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.list_knowledge_links(
            user_id=user["id"],
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            entity_type=entity_type,
            entity_id=entity_id,
            direction=direction,
            relation_type=relation_type,
            sort_by=sort,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.post("/api/knowledge/links", status_code=status.HTTP_201_CREATED)
def create_knowledge_link_endpoint(
    payload: KnowledgeLinkCreatePayload,
    request: Request,
    response: Response,
):
    """Cria uma conexão semântica direcionada entre duas entidades autorizadas."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        link = knowledge_service.create_knowledge_link(
            user_id=user["id"],
            source_type=payload.source_type,
            source_id=payload.source_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            relation_type=payload.relation_type,
            rationale=payload.rationale,
        )
        return {"status": "success", "link": KnowledgeLinkResponseModel.model_validate(link).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get("/api/knowledge/links/{link_id}")
def get_knowledge_link_endpoint(
    link_id: str,
    request: Request,
    response: Response,
):
    """Consulta uma conexão pessoal do usuário autenticado com resolução em tempo de leitura dos extremos."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        link = knowledge_service.get_knowledge_link(link_id=link_id, user_id=user["id"])
        return {"link": KnowledgeLinkResponseModel.model_validate(link).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.patch("/api/knowledge/links/{link_id}")
def update_knowledge_link_endpoint(
    link_id: str,
    payload: KnowledgeLinkUpdatePayload,
    request: Request,
    response: Response,
):
    """Atualiza o tipo de relação e/ou a justificativa autoral de uma conexão própria (extremos imutáveis)."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        link = knowledge_service.update_knowledge_link(
            link_id=link_id,
            user_id=user["id"],
            relation_type=payload.relation_type if "relation_type" in payload.model_fields_set else None,
            rationale=payload.rationale if "rationale" in payload.model_fields_set else None,
        )
        return {"status": "success", "link": KnowledgeLinkResponseModel.model_validate(link).model_dump()}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.delete("/api/knowledge/links/{link_id}")
def delete_knowledge_link_endpoint(
    link_id: str,
    request: Request,
    response: Response,
):
    """Remove uma conexão pertencente ao próprio usuário autenticado."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        res = knowledge_service.delete_knowledge_link(link_id=link_id, user_id=user["id"])
        return {"status": "success", **res}
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 4. API DE REFERÊNCIAS E BACKLINKS POR ENTIDADE
# -------------------------------------------------------------------------

@app.get(
    "/api/knowledge/entities/{entity_type}/{entity_id}/connections",
    response_model=KnowledgeEntityConnectionsResponseModel,
)
def get_entity_connections_endpoint(
    entity_type: KnowledgeEntityTypeLiteral,
    entity_id: str,
    request: Request,
    response: Response,
    direction: KnowledgeDirectionLiteral = Query(default="both"),
    relation_type: Optional[KnowledgeRelationTypeLiteral] = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """
    Lista conexões de saída e/ou backlinks recebidos de uma entidade autorizada.
    Valida primeiramente a autorização sobre a entidade consultada (anti-enumeração).
    """
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_entity_connections_and_backlinks(
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user["id"],
            direction=direction,
            relation_type=relation_type,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get(
    "/api/knowledge/entities/{entity_type}/{entity_id}/backlinks",
    response_model=KnowledgeEntityConnectionsResponseModel,
)
def get_entity_backlinks_endpoint(
    entity_type: KnowledgeEntityTypeLiteral,
    entity_id: str,
    request: Request,
    response: Response,
    relation_type: Optional[KnowledgeRelationTypeLiteral] = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """
    Endpoint dedicado para consulta de backlinks (relações de entrada) de uma entidade autorizada.
    """
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_entity_connections_and_backlinks(
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user["id"],
            direction="incoming",
            relation_type=relation_type,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 5. API DE LOCALIZAÇÃO ASSISTIDA (`/api/knowledge/lookup`)
# -------------------------------------------------------------------------

@app.get("/api/knowledge/lookup", response_model=KnowledgeLookupResponseModel)
def lookup_knowledge_entities_endpoint(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=MAX_SEARCH_QUERY_LEN),
    entity_type: Optional[KnowledgeEntityTypeLiteral] = Query(default=None),
    book_id: Optional[str] = Query(default=None, max_length=128),
    limit: int = Query(default=20, ge=1, le=AUX_SEARCH_LIMIT),
):
    """
    Localização assistida de entidades referenciáveis (`concept`, `reflection`, `highlight`, `note`, `book`)
    para criação de conexões e inserção assistida de referências.
    """
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.lookup_referencable_entities(
            user_id=user["id"],
            query=q,
            entity_type=entity_type,
            book_id=book_id,
            limit=limit,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 6. RESUMOS AGREGADOS SEM N+1 (CADERNO DE ESTUDOS E LIVROS — FASE 4)
# -------------------------------------------------------------------------

class KnowledgeSummaryEntityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["highlight", "note", "book"]
    entity_id: str = Field(..., min_length=1, max_length=128)


class KnowledgeEntitiesSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: List[KnowledgeSummaryEntityItem] = Field(..., max_length=200)


@app.get("/api/knowledge/books/{book_id}/summary")
def get_book_knowledge_summary_endpoint(
    book_id: str,
    request: Request,
    response: Response,
):
    """Retorna contagens pessoais agregadas de conhecimento conectado para uma obra acessível."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_book_knowledge_summary(
            book_id=book_id,
            user_id=user["id"],
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.post("/api/knowledge/entities/summary")
def get_entities_knowledge_summary_endpoint(
    payload: KnowledgeEntitiesSummaryPayload,
    request: Request,
    response: Response,
):
    """Retorna resumos pessoais em lote (sem N+1) para até 200 entidades autorizadas."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_entities_knowledge_summary(
            user_id=user["id"],
            entities=[item.model_dump() for item in payload.entities],
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get("/api/knowledge/summary")
def get_atlas_overview_summary_endpoint(
    request: Request,
    response: Response,
    recent_limit: int = Query(default=6, ge=1, le=20),
):
    """Retorna a visão geral agregada e estritamente pessoal do Atlas do usuário autenticado (Fase 5)."""
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_atlas_overview_summary(
            user_id=user["id"],
            recent_limit=recent_limit,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 7. PESQUISA INTEGRADA DO ATLAS E GRAFO LOCAL DE CONHECIMENTO (FASE 6)
# -------------------------------------------------------------------------

@app.get("/api/knowledge/search")
def search_knowledge_endpoint(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=MAX_SEARCH_QUERY_LEN),
    entity_types: Optional[str] = Query(default=None, max_length=128),
    book_id: Optional[str] = Query(default=None, max_length=128),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """
    Pesquisa integrada do Atlas (KD-008 — Fase 6) sobre conceitos, reflexões,
    destaques, notas, conexões semânticas e metadados de obras acessíveis,
    com autorização relacional em tempo de leitura e snippets seguros.
    """
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.search_knowledge(
            user_id=user["id"],
            query=q,
            entity_types=entity_types,
            book_id=book_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


@app.get("/api/knowledge/graph")
def get_knowledge_graph_endpoint(
    request: Request,
    response: Response,
    entity_type: KnowledgeEntityTypeLiteral = Query(...),
    entity_id: str = Query(..., min_length=1, max_length=128),
    depth: int = Query(default=DEFAULT_GRAPH_DEPTH, ge=1, le=2),
    max_nodes: int = Query(default=DEFAULT_GRAPH_MAX_NODES, ge=1, le=HARD_MAX_GRAPH_NODES),
    max_edges: int = Query(default=DEFAULT_GRAPH_MAX_EDGES, ge=1, le=HARD_MAX_GRAPH_EDGES),
    entity_types: Optional[str] = Query(default=None, max_length=128),
    relation_types: Optional[str] = Query(default=None, max_length=256),
):
    """
    Retorna o subgrafo local de conhecimento centrado em `(entity_type, entity_id)`
    limitado a 1 ou 2 saltos (`max_nodes <= 80`, `max_edges <= 160`), incluindo
    a lista textual acessível do mesmo subconjunto autorizado (KD-006 — Fase 6).
    """
    user = require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return knowledge_service.get_knowledge_graph(
            user_id=user["id"],
            entity_type=entity_type,
            entity_id=entity_id,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            entity_types=entity_types,
            relation_types=relation_types,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)


# -------------------------------------------------------------------------
# 8. EXPORTAÇÃO PORTÁTIL DO ATLAS (FASE 7 — KD-009)
# -------------------------------------------------------------------------

def _cleanup_temp_export_file(path_str: str) -> None:
    try:
        if path_str and Path(path_str).exists():
            Path(path_str).unlink()
    except OSError:
        pass


def _stream_file_and_cleanup(file_path: str, chunk_size: int = 65536):
    try:
        with open(file_path, "rb") as fp:
            while True:
                chunk = fp.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        _cleanup_temp_export_file(file_path)


@app.get("/api/knowledge/export")
def export_knowledge_atlas_endpoint(
    request: Request,
    format: Literal["markdown_zip", "json", "csv"] = Query(default="markdown_zip"),
    wikilinks: bool = Query(default=False),
    include_archived: bool = Query(default=True),
):
    """
    Exporta o conhecimento pessoal autorizado do Atlas do usuário autenticado
    em Markdown ZIP (`markdown_zip`), JSON estruturado v2.0 (`json`) ou pacote CSV (`csv`).
    """
    user = require_user(request)
    try:
        result = knowledge_export_service.generate_atlas_export_file(
            user_id=user["id"],
            export_format=format,
            wikilinks=wikilinks,
            include_archived=include_archived,
        )
    except KnowledgeError as exc:
        raise _handle_knowledge_error(exc)

    file_path = result["file_path"]
    filename = result["filename"]
    return StreamingResponse(
        _stream_file_and_cleanup(file_path),
        media_type=result["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(_cleanup_temp_export_file, file_path),
    )



# =========================================================================
# OTIMIZAÇÃO AUTOMÁTICA DE DOCUMENTOS EM SEGUNDO PLANO (FASE 12.4.1)
# =========================================================================

class DocOptimizationConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    mode: Optional[Literal["disabled", "immediate", "scheduled", "adaptive"]] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    timezone: Optional[str] = None
    weekdays: Optional[List[int]] = None
    adaptive_use_window: Optional[bool] = None
    reading_idle_seconds: Optional[int] = None
    max_storage_mb: Optional[int] = None
    min_free_disk_mb: Optional[int] = None
    min_document_mb: Optional[float] = None
    max_document_mb: Optional[float] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None
    subprocess_mem_limit_mb: Optional[int] = None


class DocOptimizationEnqueuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=500)


@app.get("/api/admin/optimization/status")
def admin_optimization_status(request: Request, response: Response):
    """Retorna configuração, disponibilidade do QPDF, contadores e uso de disco dos derivados."""
    require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    return document_optimization_service.get_status()


@app.put("/api/admin/optimization/config")
def admin_optimization_update_config(payload: DocOptimizationConfigPayload, request: Request, response: Response):
    """Atualiza política de otimização automática de documentos (somente administrador)."""
    admin = require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        updated = document_optimization_service.update_config(payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    log_admin_action(admin["id"], "update_doc_optimization_config", json.dumps(updated))
    return {"status": "success", "config": updated}


@app.post("/api/admin/optimization/enqueue")
def admin_optimization_enqueue(payload: DocOptimizationEnqueuePayload, request: Request, response: Response):
    """Analisa livros ativos e enfileira candidatos pendentes sem bloquear a leitura."""
    admin = require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    stats = document_optimization_service.enqueue_pending_books(limit=payload.limit)
    log_admin_action(admin["id"], "enqueue_doc_optimization", json.dumps(stats))
    return {"status": "success", **stats}


@app.post("/api/admin/optimization/books/{book_id}")
def admin_optimization_single_book(book_id: str, request: Request, response: Response, force: bool = Query(False)):
    """Analisa/otimiza manualmente um documento específico preservando o original."""
    admin = require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        orig_path = resolve_book_path(book_id)
    except SecurityError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except BookNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    res = document_optimization_service.optimize_single_book(book_id, orig_path, force=force)
    log_admin_action(admin["id"], "optimize_single_book", f"{book_id}:{res.get('state')}")
    return {"status": "success", "result": res}


@app.post("/api/admin/optimization/pause")
def admin_optimization_pause(request: Request, response: Response):
    require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    return document_optimization_service.pause_processing()


@app.post("/api/admin/optimization/resume")
def admin_optimization_resume(request: Request, response: Response):
    require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    return document_optimization_service.resume_processing()


@app.post("/api/admin/optimization/cancel")
def admin_optimization_cancel(request: Request, response: Response):
    require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    return document_optimization_service.cancel_processing()


@app.post("/api/admin/optimization/cleanup")
def admin_optimization_cleanup(request: Request, response: Response):
    """Remove todos os derivados regeneráveis sem tocar nos arquivos originais."""
    admin = require_admin(request)
    response.headers["Cache-Control"] = "private, no-store"
    res = document_optimization_service.cleanup_all_derivatives()
    log_admin_action(admin["id"], "cleanup_doc_derivatives", json.dumps(res))
    return {"status": "success", **res}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  # Estritamente 1 worker para baixo consumo de memória
        reload=False,
    )
