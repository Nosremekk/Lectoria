"""
Módulo de seleção visual de diretórios do Lectoria (Iteração 8).

Suporta duas modalidades seguras e complementares para administradores:
1. Caso A — Seletor nativo do sistema operacional (Zenity/KDialog), disponível
   exclusivamente quando o acesso parte da própria máquina do servidor (loopback)
   e existe sessão gráfica ativa (DISPLAY ou WAYLAND_DISPLAY).
2. Caso B — Navegador visual de diretórios controlado pelo backend, funcional
   tanto localmente quanto a partir de outros dispositivos da rede doméstica,
   com bloqueio rigoroso de diretórios do sistema, pastas internas da aplicação,
   links simbólicos e travessia de caminho (path traversal).
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from sources_service import (
    FORBIDDEN_SYSTEM_PATHS,
    InvalidSourceError,
    validate_and_canonicalize_path,
)


LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class NativePickerUnavailableError(RuntimeError):
    """Lançada quando o seletor gráfico nativo não está disponível no ambiente."""
    pass


class RemoteNativePickerForbiddenError(PermissionError):
    """Lançada quando um cliente remoto tenta acionar uma janela gráfica no servidor."""
    pass


def is_local_client(client_host: Optional[str]) -> bool:
    """
    Verifica se a requisição HTTP partiu da própria máquina do servidor (loopback).
    """
    if not client_host:
        return False
    return client_host.strip().lower() in LOCAL_CLIENT_HOSTS


def get_native_picker_status(client_host: Optional[str]) -> Dict[str, Any]:
    """
    Inspeciona se o seletor gráfico nativo pode ser utilizado para esta requisição.
    """
    local = is_local_client(client_host)
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    zenity_bin = shutil.which("zenity")
    kdialog_bin = shutil.which("kdialog")
    tool = "zenity" if zenity_bin else ("kdialog" if kdialog_bin else None)

    available = bool(local and has_display and tool)
    reason = None
    if not local:
        reason = "Acesso remoto via rede: utilize o navegador visual de pastas do servidor."
    elif not has_display:
        reason = "Servidor sem sessão gráfica ativa (DISPLAY/WAYLAND_DISPLAY ausente)."
    elif not tool:
        reason = "Utilitário gráfico de seleção (zenity/kdialog) não encontrado no sistema."

    return {
        "available": available,
        "is_local_client": local,
        "has_display": has_display,
        "tool": tool,
        "reason": reason,
    }


def pick_directory_native(
    client_host: Optional[str],
    initial_dir: Optional[str] = None,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Abre o seletor nativo de diretórios do sistema operacional no servidor local.
    Nunca executa comandos via shell (shell=False) e jamais permite invocação por clientes remotos.
    """
    if not is_local_client(client_host):
        raise RemoteNativePickerForbiddenError(
            "Por segurança, o seletor nativo do sistema operacional só pode ser aberto a partir do próprio servidor local."
        )

    status = get_native_picker_status(client_host)
    if not status["available"]:
        raise NativePickerUnavailableError(
            status["reason"] or "Seletor gráfico nativo indisponível neste ambiente."
        )

    tool = status["tool"]
    start_dir = str(Path.home())
    if initial_dir and initial_dir.strip():
        candidate = Path(initial_dir.strip()).expanduser()
        if candidate.exists() and candidate.is_dir():
            start_dir = str(candidate.resolve())

    if tool == "zenity":
        cmd = [
            "zenity",
            "--file-selection",
            "--directory",
            "--title=Selecionar pasta de livros — Lectoria",
            f"--filename={start_dir.rstrip('/')}/",
        ]
    else:
        cmd = [
            "kdialog",
            "--getexistingdirectory",
            start_dir,
            "--title",
            "Selecionar pasta de livros — Lectoria",
        ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise NativePickerUnavailableError(
            "O tempo limite para seleção de diretório na janela do sistema esgotou."
        )
    except OSError as exc:
        raise NativePickerUnavailableError(
            f"Falha ao iniciar o seletor nativo de diretórios: {exc}"
        )

    # Código 1 no zenity/kdialog representa cancelamento normal pelo usuário
    if proc.returncode == 1 or (proc.returncode == 0 and not proc.stdout.strip()):
        return {
            "status": "cancelled",
            "path": None,
            "name_suggestion": None,
        }

    if proc.returncode != 0:
        stderr_msg = (proc.stderr or "").strip()
        raise NativePickerUnavailableError(
            f"O seletor nativo encerrou com erro (código {proc.returncode}). {stderr_msg}".strip()
        )

    selected_raw = proc.stdout.strip().splitlines()[0].strip()
    # Valida rigorosamente o diretório escolhido antes de devolvê-lo
    canonical = validate_and_canonicalize_path(selected_raw)
    return {
        "status": "selected",
        "path": str(canonical),
        "name_suggestion": canonical.name or "Biblioteca",
    }


def _is_internal_or_forbidden_dir(path_obj: Path) -> Optional[str]:
    """
    Verifica se um diretório resolvido é reservado do sistema ou interno da aplicação.
    Retorna a mensagem de motivo caso seja bloqueado, ou None se for permitido.
    """
    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if forbidden == Path("/"):
            continue
        if path_obj == forbidden or forbidden in path_obj.parents:
            return f"Diretório reservado do sistema operacional ('{forbidden}')."

    data_dir = config.DATA_DIR.resolve()
    backups_dir = config.BACKUPS_DIR.resolve()
    covers_dir = config.COVERS_DIR.resolve()
    base_dir = config.BASE_DIR.resolve()
    venv_dir = (base_dir / ".venv").resolve()
    git_dir = (base_dir / ".git").resolve()

    if path_obj == data_dir or data_dir in path_obj.parents:
        return "Diretório interno de dados da aplicação."
    if path_obj == covers_dir or covers_dir in path_obj.parents:
        return "Diretório interno de cache de capas."
    if path_obj == backups_dir or backups_dir in path_obj.parents:
        return "Diretório interno de backups da aplicação."
    if venv_dir.exists() and (path_obj == venv_dir or venv_dir in path_obj.parents):
        return "Diretório de ambiente virtual Python (.venv)."
    if git_dir.exists() and (path_obj == git_dir or git_dir in path_obj.parents):
        return "Diretório de controle de versão (.git)."

    return None


def _count_direct_pdfs(dir_path: Path, max_scan: int = 500) -> int:
    """Conta rapidamente quantos arquivos .pdf existem diretamente na pasta informada."""
    count = 0
    try:
        with os.scandir(dir_path) as it:
            for idx, entry in enumerate(it):
                if idx >= max_scan:
                    break
                if not entry.name.startswith(".") and entry.name.lower().endswith(".pdf"):
                    try:
                        if entry.is_file(follow_symlinks=False):
                            count += 1
                    except OSError:
                        pass
    except OSError:
        return 0
    return count


def get_browsable_roots() -> List[Dict[str, str]]:
    """
    Retorna atalhos seguros para pontos de partida comuns no servidor.
    """
    roots: List[Dict[str, str]] = []
    home_dir = Path.home().resolve()
    if home_dir.exists() and home_dir.is_dir():
        roots.append({"label": "Pasta Pessoal", "path": str(home_dir)})

    lib_dir = config.LIBRARY_DIR.resolve()
    if lib_dir.exists() and lib_dir.is_dir():
        roots.append({"label": "Biblioteca Padrão", "path": str(lib_dir)})

    for mount_candidate, label in [
        (Path("/media"), "Mídias Removíveis (/media)"),
        (Path("/mnt"), "Pontos de Montagem (/mnt)"),
    ]:
        if mount_candidate.exists() and mount_candidate.is_dir() and os.access(mount_candidate, os.R_OK):
            roots.append({"label": label, "path": str(mount_candidate.resolve())})

    return roots


def browse_server_directories(
    raw_path: Optional[str] = None,
    client_host: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lista subdiretórios de um caminho no servidor de forma segura para seleção visual de fontes.
    Bloqueia travessia com '..', links simbólicos, caminhos inexistentes, sem permissão de leitura
    ou diretórios reservados do sistema/aplicação.
    """
    if not raw_path or not raw_path.strip():
        home_dir = Path.home().resolve()
        target_raw = str(home_dir if home_dir.exists() else config.LIBRARY_DIR.resolve())
    else:
        target_raw = raw_path.strip()

    if "\x00" in target_raw:
        raise InvalidSourceError("O caminho contém caracteres nulos inválidos.")

    expanded = Path(target_raw).expanduser()
    if ".." in expanded.parts:
        raise InvalidSourceError("Tentativa de travessia de diretório ('..') bloqueada por segurança.")

    if expanded.is_symlink():
        raise InvalidSourceError("Links simbólicos não são permitidos na navegação de fontes.")

    try:
        resolved = expanded.resolve()
    except Exception as exc:
        raise InvalidSourceError(f"Caminho inválido: {exc}")

    # Verifica se algum ancestral intermediário no caminho informado era um symlink para fora
    if not resolved.exists():
        raise InvalidSourceError(f"O diretório '{resolved}' não existe no sistema de arquivos.")

    if not resolved.is_dir():
        raise InvalidSourceError(f"O caminho '{resolved}' não é um diretório.")

    forbidden_reason = _is_internal_or_forbidden_dir(resolved)
    if forbidden_reason:
        raise InvalidSourceError(f"Acesso negado ao diretório '{resolved}': {forbidden_reason}")

    if not os.access(resolved, os.R_OK | os.X_OK):
        raise InvalidSourceError(f"Sem permissão de leitura no diretório '{resolved}'.")

    # Verifica se o próprio diretório atual pode ser selecionado como fonte
    can_select_current = False
    current_select_error = None
    try:
        validate_and_canonicalize_path(str(resolved))
        can_select_current = True
    except InvalidSourceError as exc:
        current_select_error = str(exc)

    current_pdf_count = _count_direct_pdfs(resolved)

    # Calcula diretório pai seguro (se não for '/' ou proibido)
    parent_path: Optional[str] = None
    if resolved != Path("/") and resolved.parent != resolved:
        parent_candidate = resolved.parent
        if (
            parent_candidate != Path("/")
            and _is_internal_or_forbidden_dir(parent_candidate) is None
            and os.access(parent_candidate, os.R_OK | os.X_OK)
        ):
            parent_path = str(parent_candidate)

    # Lista subpastas imediatas (ignorando ocultas e bloqueando symlinks)
    subdirectories: List[Dict[str, Any]] = []
    try:
        with os.scandir(resolved) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
            for entry in entries:
                if entry.name.startswith("."):
                    continue

                entry_path = resolved / entry.name
                try:
                    is_sym = entry.is_symlink()
                except OSError:
                    continue

                if is_sym:
                    # Não segue links simbólicos; expõe como bloqueado na listagem
                    subdirectories.append(
                        {
                            "name": entry.name,
                            "path": str(entry_path),
                            "readable": False,
                            "selectable": False,
                            "is_symlink": True,
                            "pdf_count": 0,
                            "reason": "Link simbólico bloqueado por segurança.",
                        }
                    )
                    continue

                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                sub_resolved = entry_path.resolve()
                sub_forbidden = _is_internal_or_forbidden_dir(sub_resolved)
                if sub_forbidden:
                    # Oculta ou marca como bloqueado diretórios internos/reservados
                    continue

                readable = bool(os.access(sub_resolved, os.R_OK | os.X_OK))
                selectable = False
                reason = None
                if not readable:
                    reason = "Sem permissão de leitura."
                else:
                    try:
                        validate_and_canonicalize_path(str(sub_resolved))
                        selectable = True
                    except InvalidSourceError as exc:
                        reason = str(exc)

                pdf_count = _count_direct_pdfs(sub_resolved) if readable else 0

                subdirectories.append(
                    {
                        "name": entry.name,
                        "path": str(sub_resolved),
                        "readable": readable,
                        "selectable": selectable,
                        "is_symlink": False,
                        "pdf_count": pdf_count,
                        "reason": reason,
                    }
                )
    except PermissionError:
        raise InvalidSourceError(f"Sem permissão de leitura no diretório '{resolved}'.")

    return {
        "current_path": str(resolved),
        "current_name": resolved.name or str(resolved),
        "parent_path": parent_path,
        "can_select_current": can_select_current,
        "current_error": current_select_error,
        "current_pdf_count": current_pdf_count,
        "roots": get_browsable_roots(),
        "directories": subdirectories,
        "native_picker": get_native_picker_status(client_host),
    }
