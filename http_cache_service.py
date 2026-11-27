"""
Serviço de otimização HTTP da Lectoria (Fase 12.2):
1. Compressão HTTP seletiva (gzip nível 5) restrita a tipos textuais elegíveis,
   preservando integralmente HTTP Range/206, HEAD, 304, downloads, binários e segurança.
2. Manifesto de assets estáticos baseado em hash SHA-256 de conteúdo (100% em memória,
   compatível com ReadonlyRootfs=true) para habilitar Cache-Control: immutable seguro
   mesmo entre builds com o mesmo APP_VERSION.
3. Helpers RFC 9110 para validação condicional de ETag / If-None-Match.
"""

from __future__ import annotations

import gzip
import hashlib
import html as html_lib
import json
from pathlib import Path
import re
import secrets
import threading
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Tipos MIME textuais elegíveis para compressão HTTP (allowlist estrita).
# Nunca incluir PDF, EPUB, JPEG, PNG, WebP, ZIP ou octet-stream.
COMPRESSIBLE_MEDIA_TYPES = frozenset(
    {
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "application/json",
        "text/plain",
        "text/xml",
        "application/xml",
        "image/svg+xml",
    }
)

# Rotas excluídas de compressão por segurança (mitigação BREACH em fluxos de autenticação)
# ou por serem binários/arquivos já compactados/streaming.
EXCLUDED_PATH_PREFIXES = (
    "/api/auth/",
    "/api/backups/",
    "/api/knowledge/export",
)
EXCLUDED_PATH_SUFFIXES = (
    "/file",
    "/download",
    "/cover",
)

# Assets carregados dinamicamente por JS/Workers (expostos via <meta> CSP-safe no HTML)
DYNAMIC_ASSET_PATHS = (
    "/static/vendor/pdfjs/pdf.worker.min.js",
    "/static/vendor/epubjs/jszip.min.js",
    "/static/vendor/epubjs/epub.min.js",
)

_STATIC_ATTR_RE = re.compile(
    r'(?P<attr>\b(?:href|src))="(?P<path>/static/[^"?#]+)(?P<query>\?[^"]*)?"'
)


def parse_accept_encoding_gzip(header_value: str) -> bool:
    """
    Avalia se o cabeçalho Accept-Encoding aceita gzip respeitando q-values (RFC 9110).
    Retorna False quando ausente ou quando gzip possui q=0.
    """
    if not header_value:
        return False
    for part in header_value.split(","):
        token = part.strip().lower()
        if not token:
            continue
        pieces = [p.strip() for p in token.split(";")]
        coding = pieces[0]
        if coding not in ("gzip", "x-gzip"):
            continue
        q_val = 1.0
        for param in pieces[1:]:
            if param.startswith("q="):
                try:
                    q_val = float(param[2:].strip())
                except ValueError:
                    q_val = 0.0
        if q_val > 0.0:
            return True
    return False


def append_vary_header(headers: MutableHeaders, token: str) -> None:
    """Adiciona `token` ao cabeçalho Vary sem duplicar valores existentes."""
    existing = headers.get("vary")
    if not existing:
        headers["vary"] = token
        return
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    lower_parts = {p.lower() for p in parts}
    if "*" in lower_parts or token.lower() in lower_parts:
        return
    parts.append(token)
    headers["vary"] = ", ".join(parts)


def etag_matches_if_none_match(if_none_match: Optional[str], current_etag: str) -> bool:
    """
    Implementa comparação fraca RFC 9110 §13.1.2 para If-None-Match,
    suportando múltiplos ETags separados por vírgula, curinga '*' e prefixo W/.
    """
    if not if_none_match or not current_etag:
        return False
    raw = if_none_match.strip()
    if raw == "*":
        return True

    def _strip_weak(tag: str) -> str:
        t = tag.strip()
        if t.startswith("W/") or t.startswith("w/"):
            t = t[2:].strip()
        return t

    target = _strip_weak(current_etag)
    for candidate in raw.split(","):
        if _strip_weak(candidate) == target:
            return True
    return False


class AssetManifest:
    """
    Manifesto em memória de hashes SHA-256 (12 hex chars) dos arquivos em static/.
    Detecta automaticamente alterações no disco via (mtime_ns, size, ino), garantindo
    invalidação confiável mesmo entre dois builds com o mesmo APP_VERSION, sem escrever
    nada no filesystem (totalmente compatível com ReadonlyRootfs=true).
    """

    def __init__(self, static_dir: Path) -> None:
        self.static_dir = static_dir.resolve()
        self._lock = threading.Lock()
        self._hash_cache: Dict[str, Tuple[int, int, int, str]] = {}
        self._html_cache: Dict[str, Tuple[Tuple, bytes, str]] = {}

    def invalidate(self) -> None:
        """Limpa caches em memória (útil em testes ou trocas de diretório)."""
        with self._lock:
            self._hash_cache.clear()
            self._html_cache.clear()

    def _resolve_static_file(self, rel_path: str) -> Optional[Path]:
        clean = rel_path.lstrip("/")
        if clean.startswith("static/"):
            clean = clean[len("static/"):]
        try:
            candidate = (self.static_dir / clean).resolve()
        except Exception:
            return None
        if not candidate.is_relative_to(self.static_dir) or not candidate.is_file():
            return None
        return candidate

    def get_hash_for_relpath(self, rel_path: str) -> Optional[str]:
        """Retorna os 12 primeiros caracteres hex do SHA-256 do arquivo estático."""
        file_path = self._resolve_static_file(rel_path)
        if file_path is None:
            return None
        # Nunca tratar documentos HTML como assets imutáveis
        if file_path.suffix.lower() in (".html", ".htm"):
            return None
        try:
            st = file_path.stat()
        except OSError:
            return None

        key = file_path.relative_to(self.static_dir).as_posix()
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        with self._lock:
            cached = self._hash_cache.get(key)
            if cached and cached[:3] == sig:
                return cached[3]

        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]
        with self._lock:
            self._hash_cache[key] = (sig[0], sig[1], sig[2], digest)
        return digest

    def build_asset_url(self, static_url_path: str, app_version: str) -> str:
        """Constrói URL versionada /static/...?v=<APP_VERSION>&h=<sha256_12>."""
        rel = static_url_path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        content_hash = self.get_hash_for_relpath(rel)
        if not content_hash:
            return static_url_path
        return f"/static/{rel}?v={app_version}&h={content_hash}"

    def build_dynamic_manifest(self, app_version: str) -> Dict[str, str]:
        """Mapa de assets carregados dinamicamente por JS/Workers."""
        result: Dict[str, str] = {}
        for path in DYNAMIC_ASSET_PATHS:
            url = self.build_asset_url(path, app_version)
            if url != path:
                result[path] = url
        return result

    def render_html(self, html_path: Path, app_version: str) -> Tuple[bytes, str]:
        """
        Renderiza um arquivo HTML (index.html ou reader.html) reescrevendo referências
        /static/* com ?v=<APP_VERSION>&h=<hash> e injetando <meta name="lectoria-asset-manifest">
        (100% compatível com CSP script-src 'self', sem inline scripts).
        Retorna (html_bytes, etag).
        """
        resolved = html_path.resolve()
        st = resolved.stat()
        dyn_manifest = self.build_dynamic_manifest(app_version)

        # Assinatura composta pelo HTML + hashes de todos os assets referenciados
        raw_text = resolved.read_text(encoding="utf-8")
        referenced_hashes: List[Tuple[str, Optional[str]]] = []

        def _replace(match: re.Match) -> str:
            attr = match.group("attr")
            path = match.group("path")
            h = self.get_hash_for_relpath(path)
            referenced_hashes.append((path, h))
            if not h:
                query = match.group("query") or ""
                return f'{attr}="{path}{query}"'
            return f'{attr}="{path}?v={app_version}&h={h}"'

        rewritten = _STATIC_ATTR_RE.sub(_replace, raw_text)
        cache_sig = (
            st.st_mtime_ns,
            st.st_size,
            app_version,
            tuple(referenced_hashes),
            tuple(sorted(dyn_manifest.items())),
        )
        cache_key = str(resolved)
        with self._lock:
            cached = self._html_cache.get(cache_key)
            if cached and cached[0] == cache_sig:
                return cached[1], cached[2]

        meta_json = html_lib.escape(json.dumps(dyn_manifest, separators=(",", ":")), quote=True)
        meta_tag = f'  <meta name="lectoria-asset-manifest" content="{meta_json}">\n'
        if "</head>" in rewritten:
            rewritten = rewritten.replace("</head>", f"{meta_tag}</head>", 1)

        rendered_bytes = rewritten.encode("utf-8")
        etag = f'"{hashlib.sha256(rendered_bytes).hexdigest()[:16]}"'
        with self._lock:
            self._html_cache[cache_key] = (cache_sig, rendered_bytes, etag)
        return rendered_bytes, etag


class HashedStaticFiles(StaticFiles):
    """
    StaticFiles com política de cache imutável estrita:
    - Aplica `Cache-Control: public, max-age=31536000, immutable` SOMENTE quando
      o parâmetro `?h=<hash>` coincide exatamente com o SHA-256 atual do conteúdo.
    - Sem `?h=` ou com hash divergente/obsoleto, aplica `Cache-Control: public, no-cache`.
    - Arquivos `.html` servidos sob `/static/` nunca recebem `immutable`.
    """

    def __init__(self, *args, manifest: AssetManifest, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.manifest = manifest

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code not in (200, 304):
            return response

        if path.lower().endswith((".html", ".htm")):
            response.headers["Cache-Control"] = "no-cache"
            return response

        qs_bytes = scope.get("query_string", b"")
        provided_h = ""
        if qs_bytes:
            parsed = parse_qs(qs_bytes.decode("latin-1"), keep_blank_values=False)
            vals = parsed.get("h")
            if vals:
                provided_h = vals[0].strip()

        expected_h = self.manifest.get_hash_for_relpath(path)
        if expected_h and provided_h and secrets.compare_digest(provided_h, expected_h):
            cache_ctrl = "public, max-age=31536000, immutable"
        else:
            cache_ctrl = "public, no-cache"

        if expected_h:
            etag = f'"{expected_h}"'
            req_headers = Headers(scope=scope)
            if etag_matches_if_none_match(req_headers.get("if-none-match"), etag):
                return Response(
                    status_code=304,
                    headers={
                        "Cache-Control": cache_ctrl,
                        "ETag": etag,
                        "Vary": "Accept-Encoding",
                    },
                )
            response.headers["ETag"] = etag

        response.headers["Cache-Control"] = cache_ctrl
        return response


class SelectiveGZipMiddleware:
    """
    Middleware ASGI de compressão gzip seletiva (nível 5 por padrão).
    - Comprime apenas respostas GET 200 com Content-Type na allowlist textual e tamanho >= minimum_size.
    - Nunca comprime HEAD, 206 Partial Content, 304, Range, downloads (attachment),
      imagens, PDFs, EPUBs, arquivos já compactados ou rotas de autenticação (/api/auth/*).
    - Mantém cache em memória limitado (<= 64 entradas) apenas para assets /static/*
      indexados por (path, etag), eliminando recompressão repetida em CPU da VPS.
    - Garante Vary: Accept-Encoding em respostas elegíveis e preserva todos os cabeçalhos de segurança.
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compresslevel: int = 5,
        max_buffer_bytes: int = 16 * 1024 * 1024,
        static_cache_max_entries: int = 64,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.max_buffer_bytes = max_buffer_bytes
        self.static_cache_max_entries = static_cache_max_entries
        self._static_gz_cache: Dict[Tuple[str, str], bytes] = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _is_path_excluded(path: str) -> bool:
        if any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            return True
        if any(path.endswith(suffix) for suffix in EXCLUDED_PATH_SUFFIXES):
            return True
        return False

    @staticmethod
    def _is_media_type_eligible(content_type: str) -> bool:
        if not content_type:
            return False
        media_type = content_type.split(";", 1)[0].strip().lower()
        return media_type in COMPRESSIBLE_MEDIA_TYPES

    def _compress_payload(self, path: str, etag: str, raw_body: bytes) -> bytes:
        if path.startswith("/static/") and etag:
            cache_key = (path, etag)
            with self._cache_lock:
                cached = self._static_gz_cache.get(cache_key)
                if cached is not None:
                    return cached
            gz = gzip.compress(raw_body, compresslevel=self.compresslevel, mtime=0)
            with self._cache_lock:
                if len(self._static_gz_cache) >= self.static_cache_max_entries:
                    self._static_gz_cache.clear()
                self._static_gz_cache[cache_key] = gz
            return gz
        return gzip.compress(raw_body, compresslevel=self.compresslevel, mtime=0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = (scope.get("method") or "GET").upper()
        path = scope.get("path") or ""
        if method != "GET" or self._is_path_excluded(path):
            await self.app(scope, receive, send)
            return

        req_headers = dict(scope.get("headers") or [])
        accept_enc = req_headers.get(b"accept-encoding", b"").decode("latin-1")
        has_range_req = b"range" in req_headers
        if has_range_req:
            await self.app(scope, receive, send)
            return

        client_wants_gzip = parse_accept_encoding_gzip(accept_enc)

        start_message: Optional[Message] = None
        should_buffer = False
        body_chunks: List[bytes] = []
        buffered_size = 0
        passthrough_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, should_buffer, buffered_size, passthrough_started

            msg_type = message["type"]
            if msg_type == "http.response.start":
                status_code = message.get("status", 200)
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "")
                content_encoding = headers.get("content-encoding", "")
                content_range = headers.get("content-range", "")
                content_disp = headers.get("content-disposition", "").lower()
                cache_control = headers.get("cache-control", "").lower()

                eligible = (
                    status_code == 200
                    and not content_encoding
                    and not content_range
                    and "attachment" not in content_disp
                    and "no-transform" not in cache_control
                    and self._is_media_type_eligible(content_type)
                )

                if eligible:
                    append_vary_header(headers, "Accept-Encoding")
                    if client_wants_gzip:
                        start_message = message
                        should_buffer = True
                        return

                await send(message)
                return

            if msg_type == "http.response.body":
                if not should_buffer:
                    await send(message)
                    return

                chunk = message.get("body", b"") or b""
                more_body = bool(message.get("more_body", False))

                if passthrough_started:
                    await send(message)
                    return

                if buffered_size + len(chunk) > self.max_buffer_bytes:
                    # Resposta textual excepcionalmente grande: fallback seguro sem compressão
                    passthrough_started = True
                    assert start_message is not None
                    await send(start_message)
                    for prev in body_chunks:
                        if prev:
                            await send({"type": "http.response.body", "body": prev, "more_body": True})
                    body_chunks.clear()
                    await send(message)
                    return

                if chunk:
                    body_chunks.append(chunk)
                    buffered_size += len(chunk)

                if more_body:
                    return

                raw_body = b"".join(body_chunks)
                body_chunks.clear()
                assert start_message is not None
                headers = MutableHeaders(scope=start_message)

                if len(raw_body) >= self.minimum_size:
                    etag_val = headers.get("etag", "")
                    gz_body = self._compress_payload(path, etag_val, raw_body)
                    if len(gz_body) < len(raw_body):
                        headers["content-encoding"] = "gzip"
                        headers["content-length"] = str(len(gz_body))
                        if etag_val and not etag_val.startswith(("W/", "w/")):
                            headers["etag"] = f"W/{etag_val}"
                        await send(start_message)
                        await send({"type": "http.response.body", "body": gz_body, "more_body": False})
                        return

                await send(start_message)
                await send({"type": "http.response.body", "body": raw_body, "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
