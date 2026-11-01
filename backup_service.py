"""
Módulo de serviço de backup e integridade do banco de dados SQLite do Lectoria.
Utiliza a SQLite Online Backup API para gerar cópias pontuais e consistentes
sem risco de corrupção e sem bloquear leituras/escritas.
"""

from datetime import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any, Dict, List, Optional

import config

# Trava de concorrência para garantir que apenas uma operação de backup ou restauração ocorra por vez
_backup_lock = threading.Lock()


class BackupError(Exception):
    """Exceção levantada quando ocorre um erro na criação ou restauração de backup."""
    pass


def verify_sqlite_integrity(db_path: Path) -> bool:
    """
    Executa verificação de integridade no banco de dados SQLite indicado.
    Retorna True se estiver 100% íntegro ("ok"), False caso contrário.
    """
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        conn.close()
        return bool(result and result[0] == "ok")
    except Exception:
        return False


def create_backup(custom_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Gera um backup consistente do banco de dados SQLite da aplicação em backups/.
    Utiliza sqlite3.Connection.backup para garantir consistência de páginas mesmo em modo WAL.
    Protegido por trava de exclusão mútua contra concorrência.
    """
    with _backup_lock:
        if not config.DB_PATH.exists():
            raise BackupError(f"Banco de dados de origem '{config.DB_PATH}' não existe.")

        config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if custom_name:
            clean_name = "".join(c for c in custom_name if c.isalnum() or c in ("-", "_"))
            backup_filename = f"lectoria_backup_{clean_name}_{timestamp}.db"
        else:
            backup_filename = f"lectoria_backup_{timestamp}.db"

        target_path = config.BACKUPS_DIR / backup_filename

        # Executa a cópia de segurança usando a API nativa de backup do SQLite
        try:
            source_conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
            target_conn = sqlite3.connect(str(target_path))

            source_conn.backup(target_conn)

            target_conn.close()
            source_conn.close()
        except Exception as e:
            if target_path.exists():
                target_path.unlink()
            raise BackupError(f"Falha ao executar backup SQLite: {e}") from e

        # Verifica integridade da cópia gerada
        if not verify_sqlite_integrity(target_path):
            if target_path.exists():
                target_path.unlink()
            raise BackupError("A cópia de backup gerada falhou na verificação de integridade.")

        stat = target_path.stat()
        return {
            "filename": backup_filename,
            "path": str(target_path),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "integrity": "ok",
        }


def list_backups() -> List[Dict[str, Any]]:
    """Lista todos os backups disponíveis no diretório backups/, ordenados do mais recente ao mais antigo."""
    if not config.BACKUPS_DIR.exists():
        return []

    backups = []
    for file in config.BACKUPS_DIR.glob("*.db"):
        if file.is_file():
            stat = file.stat()
            backups.append({
                "filename": file.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "mtime": stat.st_mtime,
            })

    backups.sort(key=lambda x: x["mtime"], reverse=True)
    for b in backups:
        b.pop("mtime", None)
    return backups


def restore_backup(backup_filename: str) -> Dict[str, Any]:
    """
    Restaura um backup previamente gerado para data/lectoria.db.
    Realiza verificação rigorosa de integridade antes da substituição, gera backup prévio de segurança
    para reversão em caso de falha e limpa arquivos residuais WAL.
    """
    with _backup_lock:
        backup_path = (config.BACKUPS_DIR / backup_filename).resolve()

        # Validação de segurança de caminho
        if not backup_path.is_relative_to(config.BACKUPS_DIR):
            raise BackupError("Caminho de backup inválido.")

        if not backup_path.exists() or not backup_path.is_file():
            raise BackupError(f"Arquivo de backup '{backup_filename}' não encontrado.")

        # 1. Validação prévia de integridade do arquivo de backup
        if not verify_sqlite_integrity(backup_path):
            raise BackupError(f"O arquivo de backup '{backup_filename}' está corrompido ou é inválido.")

        # 2. Garante diretório de destino
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 3. Cria cópia de segurança do banco atual antes de tocar nos dados
        safety_backup_path = config.DATA_DIR / "lectoria_pre_restore.db.bak"
        has_original_db = config.DB_PATH.exists() and config.DB_PATH.is_file()
        if has_original_db:
            try:
                shutil.copy2(config.DB_PATH, safety_backup_path)
            except Exception as e:
                raise BackupError(f"Não foi possível criar cópia de proteção do banco atual: {e}")

        # 4. Substituição segura
        temp_target = config.DATA_DIR / "lectoria_restoring.db"
        shutil.copy2(backup_path, temp_target)

        # Verifica integridade da cópia recém-colocada no diretório destino
        if not verify_sqlite_integrity(temp_target):
            if temp_target.exists():
                temp_target.unlink()
            raise BackupError("Falha na integridade dos dados copiados para o destino.")

        try:
            # Substitui atomicamente
            temp_target.replace(config.DB_PATH)

            # 5. Remove arquivos WAL e SHM órfãos da versão anterior
            for ext in ("-wal", "-shm"):
                wal_file = config.DB_PATH.with_name(config.DB_PATH.name + ext)
                if wal_file.exists():
                    try:
                        wal_file.unlink()
                    except Exception:
                        pass

            # Verifica integridade final após substituição
            if not verify_sqlite_integrity(config.DB_PATH):
                # Restaura da cópia de segurança se falhar
                if has_original_db and safety_backup_path.exists():
                    shutil.copy2(safety_backup_path, config.DB_PATH)
                raise BackupError("O banco restaurado falhou no teste de integridade pós-substituição. Base original recuperada.")

        except Exception as e:
            # Se ocorrer erro no replace, reverte para a cópia de proteção
            if has_original_db and safety_backup_path.exists():
                try:
                    shutil.copy2(safety_backup_path, config.DB_PATH)
                except Exception:
                    pass
            if temp_target.exists():
                temp_target.unlink()
            raise BackupError(f"Falha durante a substituição dos dados: {e}")

        stat = config.DB_PATH.stat()
        return {
            "status": "success",
            "restored_from": backup_filename,
            "target_path": str(config.DB_PATH),
            "safety_backup": str(safety_backup_path) if safety_backup_path.exists() else None,
            "size_bytes": stat.st_size,
            "verified_integrity": True,
        }


def prune_backups(keep_count: int) -> int:
    """
    Remove os backups mais antigos além da quantidade indicada em keep_count.
    Executado exclusivamente mediante comando explícito do usuário.
    Nunca apaga backups automaticamente.
    """
    if keep_count < 1:
        raise BackupError("A quantidade de backups a preservar deve ser de no mínimo 1.")

    with _backup_lock:
        backups = list_backups()
        if len(backups) <= keep_count:
            return 0

        to_remove = backups[keep_count:]
        removed_count = 0
        for b in to_remove:
            b_path = config.BACKUPS_DIR / b["filename"]
            if b_path.exists() and b_path.is_file():
                try:
                    b_path.unlink()
                    removed_count += 1
                except Exception:
                    pass
        return removed_count


