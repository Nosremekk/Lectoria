"""
Módulo de autenticação, perfis de usuário e gerenciamento de sessões do Lectoria.
Utiliza hashing criptográfico scrypt (nativo do Python 3) e tokens de sessão seguros.
Não requer serviços externos nem bibliotecas pesadas.
"""

from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from database import get_db


class AuthError(Exception):
    """Exceção levantada para erros de autenticação ou permissão."""
    pass


class RateLimitError(AuthError):
    """Exceção levantada quando tentativas excessivas de login excedem o limite."""
    pass


class UserNotFoundError(Exception):
    """Exceção levantada quando um usuário não for encontrado."""
    pass


class UserAlreadyExistsError(Exception):
    """Exceção levantada quando se tenta criar usuário com username já em uso."""
    pass


# Controle de tentativas de login em memória para proteção simples contra força bruta
_FAILED_LOGINS: Dict[str, List[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300  # 5 minutos


def _check_rate_limit(key: str) -> None:
    """Verifica se o identificador (IP ou username) excedeu o limite de tentativas."""
    now = time.time()
    if key in _FAILED_LOGINS:
        # Filtra tentativas dentro da janela
        recent = [t for t in _FAILED_LOGINS[key] if now - t < LOCKOUT_DURATION_SECONDS]
        _FAILED_LOGINS[key] = recent
        if len(recent) >= MAX_FAILED_ATTEMPTS:
            wait_seconds = int(LOCKOUT_DURATION_SECONDS - (now - recent[0]))
            raise RateLimitError(
                f"Muitas tentativas incorretas. Acesso bloqueado temporariamente por {wait_seconds} segundos."
            )


def _record_failed_attempt(key: str) -> None:
    """Registra uma tentativa falha de login."""
    now = time.time()
    if key not in _FAILED_LOGINS:
        _FAILED_LOGINS[key] = []
    _FAILED_LOGINS[key].append(now)


def _clear_failed_attempts(key: str) -> None:
    """Limpa tentativas após login bem-sucedido."""
    _FAILED_LOGINS.pop(key, None)


def clear_failed_logins() -> None:
    """Limpa todas as tentativas falhas registradas (utilizado em testes)."""
    _FAILED_LOGINS.clear()


def hash_password(password: str) -> str:
    """Gera um hash scrypt seguro com salt aleatório de 16 bytes."""
    if not password or len(password) < 4:
        raise ValueError("A senha deve ter no mínimo 4 caracteres.")
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
    )
    return f"scrypt:{salt}:{key.hex()}"


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """Valida se a senha informada confere com o hash scrypt armazenado."""
    if not hashed or not password:
        return False
    try:
        parts = hashed.split(":")
        if len(parts) != 3 or parts[0] != "scrypt":
            return False
        _, salt, expected_hex = parts
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=16384,
            r=8,
            p=1,
        )
        return secrets.compare_digest(key.hex(), expected_hex)
    except Exception:
        return False


def has_admin_user() -> bool:
    """Verifica se já existe pelo menos um administrador com senha configurada no sistema."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE role = 'admin' AND is_active = 1 AND password_hash IS NOT NULL AND password_hash != ''
            """
        )
        return cursor.fetchone()[0] > 0


def create_user(
    username: str,
    password: str,
    display_name: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Cria um novo usuário no sistema."""
    clean_user = username.strip().lower()
    if not clean_user or len(clean_user) < 3:
        raise ValueError("O nome de usuário deve ter pelo menos 3 caracteres alfanuméricos.")
    if not clean_user.replace("_", "").replace("-", "").isalnum():
        raise ValueError("O nome de usuário deve conter apenas letras, números, '_' ou '-'.")
    if role not in ("admin", "reader"):
        raise ValueError("Papel de usuário inválido. Escolha 'admin' ou 'reader'.")

    pw_hash = hash_password(password)
    disp = display_name.strip() if display_name else clean_user.capitalize()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (clean_user,))
        if cursor.fetchone():
            raise UserAlreadyExistsError(f"Usuário '{clean_user}' já existe.")

        cursor.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (clean_user, disp, pw_hash, role),
        )
        user_id = cursor.lastrowid
        return {
            "id": user_id,
            "username": clean_user,
            "display_name": disp,
            "role": role,
            "is_active": 1,
        }


def authenticate_user(username: str, password: str, client_ip: str = "") -> Dict[str, Any]:
    """Valida as credenciais do usuário com controle de taxa de tentativas."""
    clean_user = username.strip().lower()
    rate_key = f"{client_ip}:{clean_user}" if client_ip else clean_user
    _check_rate_limit(rate_key)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, display_name, password_hash, role, is_active
            FROM users WHERE username = ? COLLATE NOCASE
            """,
            (clean_user,),
        )
        row = cursor.fetchone()

        if not row or not row["is_active"]:
            _record_failed_attempt(rate_key)
            raise AuthError("Credenciais inválidas ou conta desativada.")

        if not verify_password(password, row["password_hash"]):
            _record_failed_attempt(rate_key)
            raise AuthError("Credenciais inválidas ou conta desativada.")

        _clear_failed_attempts(rate_key)
        # Limpeza periódica de sessões expiradas
        cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now().isoformat(),))
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "is_active": row["is_active"],
        }


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Busca os dados de um usuário pelo ID (sem expor o hash da senha)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, display_name, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    """Lista todos os usuários cadastrados."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, display_name, role, is_active, created_at FROM users ORDER BY username ASC"
        )
        return [dict(r) for r in cursor.fetchall()]


def update_user(
    user_id: int,
    display_name: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    new_password: Optional[str] = None,
) -> Dict[str, Any]:
    """Atualiza as informações de um perfil de usuário."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, is_active FROM users WHERE id = ?", (user_id,))
        current = cursor.fetchone()
        if not current:
            raise UserNotFoundError(f"Usuário #{user_id} não encontrado.")

        # Proteção: nunca desativar ou rebaixar o único administrador ativo do sistema
        if current["role"] == "admin":
            if (role and role != "admin") or (is_active is False):
                cursor.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id != ?",
                    (user_id,),
                )
                other_admins = cursor.fetchone()[0]
                if other_admins < 1:
                    raise AuthError("Não é permitido desativar ou alterar o papel do único administrador ativo.")

        updates = []
        params = []
        if display_name is not None:
            clean_disp = display_name.strip()
            if not clean_disp:
                raise ValueError("O nome de exibição não pode ser vazio.")
            updates.append("display_name = ?")
            params.append(clean_disp)

        if role is not None:
            if role not in ("admin", "reader"):
                raise ValueError("Papel inválido.")
            updates.append("role = ?")
            params.append(role)

        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if new_password is not None and new_password.strip():
            pw_hash = hash_password(new_password)
            updates.append("password_hash = ?")
            params.append(pw_hash)

        if updates:
            params.append(user_id)
            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)

        # Se a senha foi alterada ou o usuário foi desativado, revoga todas as sessões ativas
        if (new_password is not None and new_password.strip()) or (is_active is False):
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

        cursor.execute(
            "SELECT id, username, display_name, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        return dict(cursor.fetchone())


def delete_user(user_id: int) -> bool:
    """Remove um usuário do sistema."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, is_active FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise UserNotFoundError(f"Usuário #{user_id} não encontrado.")

        if row["role"] == "admin":
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id != ?", (user_id,))
            if cursor.fetchone()[0] < 1:
                raise AuthError("Não é permitido excluir o único administrador do sistema.")

        cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True


# =========================================================================
# GERENCIAMENTO DE SESSÕES COM TOKENS CRIPTOGRÁFICOS HASHED
# =========================================================================

SESSION_COOKIE_NAME = "lectoria_session"
DEFAULT_SESSION_DAYS = 7


def _hash_session_token(token: str) -> str:
    """Gera hash SHA-256 do token para armazenamento seguro no banco."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, days: int = DEFAULT_SESSION_DAYS) -> str:
    """Gera um token de sessão criptográfico, armazena seu hash SHA-256 no SQLite e retorna o token bruto."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_session_token(token)
    expires_at = datetime.now() + timedelta(days=days)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sessions (token, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, user_id, expires_at.isoformat()),
        )
    return token


def get_user_from_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Recupera o usuário associado a um token de sessão válido e não expirado."""
    if not token or not isinstance(token, str):
        return None

    token_hash = _hash_session_token(token)
    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, u.is_active, s.expires_at
            FROM sessions s
            INNER JOIN users u ON s.user_id = u.id
            WHERE (s.token = ? OR s.token = ?) AND s.expires_at > ? AND u.is_active = 1
            """,
            (token_hash, token, now_iso),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "is_active": row["is_active"],
        }


def destroy_session(token: Optional[str]) -> bool:
    """Invalida um token de sessão (logout)."""
    if not token:
        return False
    token_hash = _hash_session_token(token)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE token = ? OR token = ?", (token_hash, token))
        count = cursor.rowcount
        # Aproveita para limpar sessões antigas expiradas
        cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now().isoformat(),))
        return count > 0


def cleanup_expired_sessions() -> int:
    """Remove tokens de sessões expiradas para manter o banco leve."""
    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso,))
        return cursor.rowcount
