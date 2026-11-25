"""
Módulo de serviço de backup e integridade do banco de dados SQLite do Lectoria.
Utiliza a SQLite Online Backup API para gerar cópias pontuais e consistentes
sem risco de corrupção e sem bloquear leituras/escritas.
"""

from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile
import threading
from typing import Any, Dict, List, Optional

import config
import database

# Trava de concorrência para garantir que apenas uma operação de backup ou restauração ocorra por vez
_backup_lock = threading.Lock()

# Tabelas estruturais mínimas exigidas para considerar um banco SQLite um backup válido do Lectoria
REQUIRED_CORE_TABLES = ("schema_migrations", "users", "books", "reading_progress")


class BackupError(Exception):
    """Exceção levantada quando ocorre um erro na criação ou restauração de backup."""
    pass


def verify_sqlite_integrity(db_path: Path) -> bool:
    """
    Executa verificação de integridade no banco de dados SQLite indicado.
    Retorna True se estiver 100% íntegro ("ok"), False caso contrário.
    """
    if not db_path.exists() or not db_path.is_file() or db_path.stat().st_size == 0:
        return False
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        return bool(result and result[0] == "ok")
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def validate_backup_sqlite(db_path: Path) -> Dict[str, Any]:
    """
    Valida estruturalmente um arquivo SQLite de backup em modo somente-leitura (sem modificar a origem):
    - Existência e tamanho > 0;
    - PRAGMA integrity_check == 'ok';
    - Presença das tabelas centrais do Lectoria (rejeita bancos vazios ou estranhos);
    - Compatibilidade de schema (1 <= schema_version <= database.SCHEMA_VERSION; sem downgrade automático).
    """
    if not db_path.exists() or not db_path.is_file():
        raise BackupError(f"Arquivo de banco de backup não encontrado: '{db_path}'.")
    if db_path.stat().st_size == 0:
        raise BackupError(f"Arquivo de backup '{db_path.name}' está vazio.")

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=10.0)
        cur = conn.cursor()

        chk = cur.execute("PRAGMA integrity_check;").fetchone()
        if not chk or chk[0] != "ok":
            raise BackupError(f"O arquivo de backup '{db_path.name}' falhou em PRAGMA integrity_check.")

        existing_tables = {
            row[0]
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()
        }
        missing_tables = [t for t in REQUIRED_CORE_TABLES if t not in existing_tables]
        if missing_tables:
            raise BackupError(
                f"Backup incompleto ou inválido: tabelas obrigatórias ausentes ({', '.join(missing_tables)})."
            )

        row = cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()
        schema_version = row[0] if row else None
        if not isinstance(schema_version, int) or schema_version < 1:
            raise BackupError("Backup inválido: versão de schema ausente em schema_migrations.")

        if schema_version > database.SCHEMA_VERSION:
            raise BackupError(
                f"Schema incompatível: o backup está na versão {schema_version}, "
                f"superior à versão suportada ({database.SCHEMA_VERSION}). "
                "Downgrade automático de schema não é permitido."
            )

        return {
            "integrity": "ok",
            "schema_version": schema_version,
        }
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"O arquivo de backup '{db_path.name}' está corrompido ou é inválido: {exc}") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _normalize_safe_tar_member_relpath(raw_name: str) -> Optional[Path]:
    """
    Valida e normaliza o caminho relativo de um membro de arquivo de capas (.tar.gz).
    Rejeita caminhos absolutos, bytes nulos, traversal ('..') e prefixos inválidos.
    Retorna None para entradas de diretório raiz ('.' ou 'covers').
    """
    if not raw_name or "\x00" in raw_name:
        raise BackupError(f"Caminho inseguro no arquivo de capas: {raw_name!r}")
    if raw_name.startswith("/") or raw_name.startswith("\\"):
        raise BackupError(f"Caminho absoluto rejeitado no arquivo de capas: {raw_name!r}")

    normalized = raw_name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    parts = [p for p in posix.parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise BackupError(f"Path traversal rejeitado no arquivo de capas: {raw_name!r}")
    if parts and ":" in parts[0]:
        raise BackupError(f"Prefixo de unidade rejeitado no arquivo de capas: {raw_name!r}")

    # Aceita tanto 'covers/<arquivo>' quanto './<arquivo>' ou '<arquivo>'
    if parts and parts[0] == "covers":
        parts = parts[1:]
    if not parts:
        return None
    return Path(*parts)


def extract_covers_archive_safely(archive_path: Path, dest_dir: Path) -> int:
    """
    Extrai um arquivo covers.tar.gz de forma segura e atômica (staging):
    - Rejeita arquivo corrompido ou não-tar;
    - Pré-valida TODOS os membros antes de gravar qualquer arquivo no destino;
    - Rejeita symlinks, hardlinks, dispositivos, FIFOs, caminhos absolutos e '..';
    - Extrai primeiro para um diretório temporário e só aplica ao destino se 100% válido.
    """
    if not archive_path.exists() or not archive_path.is_file():
        raise BackupError(f"Arquivo de capas não encontrado: '{archive_path}'.")

    validated_files: List[tuple[tarfile.TarInfo, Path]] = []
    try:
        with tarfile.open(archive_path, mode="r:*") as tf:
            members = tf.getmembers()
            for member in members:
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise BackupError(
                        f"Tipo de entrada inseguro rejeitado no arquivo de capas: '{member.name}'."
                    )
                rel = _normalize_safe_tar_member_relpath(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise BackupError(
                        f"Entrada não suportada no arquivo de capas: '{member.name}'."
                    )
                if rel is None:
                    continue
                validated_files.append((member, rel))

            with tempfile.TemporaryDirectory(prefix="lectoria_covers_stage_") as stage_tmp:
                stage_root = Path(stage_tmp).resolve()
                staged_pairs: List[tuple[Path, Path]] = []
                for member, rel in validated_files:
                    staged_path = (stage_root / rel).resolve()
                    if not staged_path.is_relative_to(stage_root):
                        raise BackupError(f"Caminho fora do destino rejeitado: '{member.name}'.")
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    src_f = tf.extractfile(member)
                    if src_f is None:
                        raise BackupError(f"Falha ao ler membro '{member.name}' do arquivo de capas.")
                    with src_f, open(staged_path, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                    staged_pairs.append((staged_path, rel))

                dest_root = dest_dir.resolve()
                dest_root.mkdir(parents=True, exist_ok=True)
                for staged_path, rel in staged_pairs:
                    final_path = (dest_root / rel).resolve()
                    if not final_path.is_relative_to(dest_root):
                        raise BackupError(f"Caminho final fora do destino: '{rel}'.")
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(staged_path, final_path)
                return len(staged_pairs)
    except BackupError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"Arquivo de capas inválido ou corrompido '{archive_path.name}': {exc}") from exc


def _invalidate_sessions(db_path: Path) -> int:
    """
    Invalida sessões persistidas em um banco recém-restaurado para impedir reuso
    de tokens antigos ou comprometidos após recuperação de desastre.
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=15.0)
        cur = conn.cursor()
        has_sessions = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions';"
        ).fetchone()
        if not has_sessions:
            return 0
        cur.execute("DELETE FROM sessions;")
        deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        return deleted
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
        source_conn: Optional[sqlite3.Connection] = None
        target_conn: Optional[sqlite3.Connection] = None
        try:
            source_conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
            target_conn = sqlite3.connect(str(target_path))
            source_conn.backup(target_conn)
        except Exception as e:
            if target_path.exists():
                target_path.unlink()
            raise BackupError(f"Falha ao executar backup SQLite: {e}") from e
        finally:
            if target_conn is not None:
                try:
                    target_conn.close()
                except Exception:
                    pass
            if source_conn is not None:
                try:
                    source_conn.close()
                except Exception:
                    pass

        # Verifica integridade da cópia gerada
        if not verify_sqlite_integrity(target_path):
            if target_path.exists():
                target_path.unlink()
            raise BackupError("A cópia de backup gerada falhou na verificação de integridade.")

        # Cópia de segurança das capas personalizadas (*_custom.*) associadas
        custom_covers_count = 0
        if config.COVERS_DIR.exists():
            custom_files = [
                f for f in config.COVERS_DIR.glob("*_custom.*")
                if f.is_file()
            ]
            if custom_files:
                covers_backup_dir = config.BACKUPS_DIR / f"{target_path.stem}_covers"
                covers_backup_dir.mkdir(parents=True, exist_ok=True)
                for cf in custom_files:
                    try:
                        shutil.copy2(cf, covers_backup_dir / cf.name)
                        custom_covers_count += 1
                    except Exception:
                        pass

        stat = target_path.stat()
        return {
            "filename": backup_filename,
            "path": str(target_path),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "integrity": "ok",
            "custom_covers_count": custom_covers_count,
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


def restore_backup(
    backup_filename: str,
    *,
    allow_overwrite: bool = True,
    invalidate_sessions: bool = True,
) -> Dict[str, Any]:
    """
    Restaura um backup previamente gerado em BACKUPS_DIR para data/lectoria.db.
    Realiza validação rigorosa de integridade e compatibilidade de schema antes da substituição,
    gera backup prévio de segurança (WAL-safe), limpa arquivos residuais WAL/SHM e invalida
    sessões antigas por padrão.
    """
    with _backup_lock:
        backup_path = (config.BACKUPS_DIR / backup_filename).resolve()

        # Validação de segurança de caminho
        if not backup_path.is_relative_to(config.BACKUPS_DIR.resolve()):
            raise BackupError("Caminho de backup inválido.")

        if not backup_path.exists() or not backup_path.is_file():
            raise BackupError(f"Arquivo de backup '{backup_filename}' não encontrado.")

        # 1. Validação estrutural, integridade e compatibilidade de schema (sem modificar origem)
        validation = validate_backup_sqlite(backup_path)

        # 2. Proteção opcional contra sobrescrita não autorizada
        has_original_db = config.DB_PATH.exists() and config.DB_PATH.is_file()
        if has_original_db and not allow_overwrite:
            raise BackupError(
                f"O banco de destino '{config.DB_PATH}' já existe. "
                "Confirme explicitamente a sobrescrita (allow_overwrite=True) para prosseguir."
            )

        # 3. Garante diretório de destino
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 4. Cria cópia de segurança consistente do banco atual antes de tocar nos dados (WAL-safe)
        safety_backup_path = config.DATA_DIR / "lectoria_pre_restore.db.bak"
        if has_original_db:
            src_safety: Optional[sqlite3.Connection] = None
            dst_safety: Optional[sqlite3.Connection] = None
            try:
                if safety_backup_path.exists():
                    safety_backup_path.unlink()
                src_safety = sqlite3.connect(str(config.DB_PATH), timeout=15.0)
                dst_safety = sqlite3.connect(str(safety_backup_path))
                src_safety.backup(dst_safety)
            except Exception:
                # Fallback para cópia física caso o banco atual esteja corrompido e não abra no SQLite
                try:
                    shutil.copy2(config.DB_PATH, safety_backup_path)
                except Exception as e:
                    raise BackupError(f"Não foi possível criar cópia de proteção do banco atual: {e}")
            finally:
                if dst_safety is not None:
                    try:
                        dst_safety.close()
                    except Exception:
                        pass
                if src_safety is not None:
                    try:
                        src_safety.close()
                    except Exception:
                        pass

        # 5. Substituição segura
        temp_target = config.DATA_DIR / "lectoria_restoring.db"
        shutil.copy2(backup_path, temp_target)

        # Verifica integridade da cópia recém-colocada no diretório destino
        if not verify_sqlite_integrity(temp_target):
            if temp_target.exists():
                temp_target.unlink()
            raise BackupError("Falha na integridade dos dados copiados para o destino.")

        invalidated_sessions_count = 0
        try:
            # Substitui atomicamente
            temp_target.replace(config.DB_PATH)

            # 6. Remove arquivos WAL e SHM órfãos da versão anterior
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

            # 7. Garante atualização de schema caso o backup seja de uma versão anterior
            database.init_db(config.DB_PATH)

            # 8. Invalida sessões restauradas por segurança (padrão: True)
            if invalidate_sessions:
                invalidated_sessions_count = _invalidate_sessions(config.DB_PATH)

            # 9. Restaura capas personalizadas associadas ao backup (se existirem)
            restored_covers_count = 0
            covers_backup_dir = config.BACKUPS_DIR / f"{backup_path.stem}_covers"
            if covers_backup_dir.exists() and covers_backup_dir.is_dir():
                config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
                for cf in covers_backup_dir.glob("*_custom.*"):
                    if cf.is_file():
                        try:
                            shutil.copy2(cf, config.COVERS_DIR / cf.name)
                            restored_covers_count += 1
                        except Exception:
                            pass

        except BackupError:
            raise
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
            "schema_version": validation["schema_version"],
            "verified_integrity": True,
            "restored_covers_count": restored_covers_count,
            "invalidated_sessions_count": invalidated_sessions_count,
        }


def restore_snapshot_to_directory(
    source: Path,
    target_data_dir: Path,
    *,
    allow_overwrite: bool = False,
    invalidate_sessions: bool = True,
) -> Dict[str, Any]:
    """
    Restaura um snapshot de backup (diretório contendo lectoria.db + covers.tar.gz opcional,
    ou arquivo .db + diretório irmão <stem>_covers/ opcional) para um diretório de dados alvo.

    Garantias de segurança:
    - Nunca modifica a origem;
    - Nunca sobrescreve um banco já existente sem allow_overwrite=True;
    - Valida integralmente SQLite, tabelas centrais, compatibilidade de schema e covers.tar.gz
      em staging ANTES de alterar o destino;
    - Gera cópia de proteção WAL-safe quando allow_overwrite=True e o destino já existe;
    - Invalida sessões restauradas por padrão (invalidate_sessions=True).
    """
    with _backup_lock:
        src = Path(source).expanduser().resolve()
        dst_dir = Path(target_data_dir).expanduser().resolve()
        dst_db = dst_dir / "lectoria.db"
        dst_covers = dst_dir / "covers"

        if not src.exists():
            raise BackupError(f"Origem de backup não encontrada: '{src}'.")

        covers_archive: Optional[Path] = None
        covers_dir_src: Optional[Path] = None

        if src.is_dir():
            src_db = src / "lectoria.db"
            if not src_db.exists() or not src_db.is_file():
                raise BackupError(
                    f"Backup incompleto: 'lectoria.db' ausente no diretório de snapshot '{src}'."
                )
            candidate_tar = src / "covers.tar.gz"
            candidate_dir = src / "covers"
            if candidate_tar.exists():
                covers_archive = candidate_tar
            elif candidate_dir.exists() and candidate_dir.is_dir():
                covers_dir_src = candidate_dir
        elif src.is_file():
            src_db = src
            sibling_covers = src.parent / f"{src.stem}_covers"
            if sibling_covers.exists() and sibling_covers.is_dir():
                covers_dir_src = sibling_covers
        else:
            raise BackupError(f"Origem de backup inválida: '{src}'.")

        # 1. Proteção contra sobrescrita sem confirmação explícita
        has_original_db = dst_db.exists() and dst_db.is_file()
        if has_original_db and not allow_overwrite:
            raise BackupError(
                f"Destino '{dst_db}' já existe. "
                "Use allow_overwrite=True (--force) para autorizar explicitamente a sobrescrita."
            )

        # 2. Valida banco SQLite da origem em modo somente-leitura
        validation = validate_backup_sqlite(src_db)

        # 3. Pré-valida e extrai capas em staging ANTES de tocar no destino
        with tempfile.TemporaryDirectory(prefix="lectoria_restore_stage_") as stage_root_str:
            stage_root = Path(stage_root_str)
            stage_covers = stage_root / "covers"
            staged_covers_count = 0

            if covers_archive is not None:
                staged_covers_count = extract_covers_archive_safely(covers_archive, stage_covers)
            elif covers_dir_src is not None:
                stage_covers.mkdir(parents=True, exist_ok=True)
                for item in covers_dir_src.iterdir():
                    if item.is_file() and not item.is_symlink():
                        shutil.copy2(item, stage_covers / item.name)
                        staged_covers_count += 1

            # 4. Prepara destino e cópia de segurança se estiver sobrescrevendo
            dst_dir.mkdir(parents=True, exist_ok=True)
            safety_backup_path = dst_dir / "lectoria_pre_restore.db.bak"
            if has_original_db:
                src_safety: Optional[sqlite3.Connection] = None
                dst_safety: Optional[sqlite3.Connection] = None
                try:
                    if safety_backup_path.exists():
                        safety_backup_path.unlink()
                    src_safety = sqlite3.connect(str(dst_db), timeout=15.0)
                    dst_safety = sqlite3.connect(str(safety_backup_path))
                    src_safety.backup(dst_safety)
                except Exception:
                    shutil.copy2(dst_db, safety_backup_path)
                finally:
                    if dst_safety is not None:
                        try:
                            dst_safety.close()
                        except Exception:
                            pass
                    if src_safety is not None:
                        try:
                            src_safety.close()
                        except Exception:
                            pass

            temp_target = dst_dir / "lectoria_restoring.db"
            shutil.copy2(src_db, temp_target)
            if not verify_sqlite_integrity(temp_target):
                temp_target.unlink(missing_ok=True)
                raise BackupError("Falha na integridade do banco copiado para o destino.")

            invalidated_sessions_count = 0
            try:
                temp_target.replace(dst_db)
                for ext in ("-wal", "-shm"):
                    wal_file = dst_db.with_name(dst_db.name + ext)
                    wal_file.unlink(missing_ok=True)

                if not verify_sqlite_integrity(dst_db):
                    if has_original_db and safety_backup_path.exists():
                        shutil.copy2(safety_backup_path, dst_db)
                    raise BackupError("Integridade falhou após substituição; banco original preservado.")

                database.init_db(dst_db)

                if invalidate_sessions:
                    invalidated_sessions_count = _invalidate_sessions(dst_db)

                if staged_covers_count > 0 and stage_covers.exists():
                    dst_covers.mkdir(parents=True, exist_ok=True)
                    for root_p, _, files in os.walk(stage_covers):
                        rel_dir = Path(root_p).relative_to(stage_covers)
                        (dst_covers / rel_dir).mkdir(parents=True, exist_ok=True)
                        for fname in files:
                            shutil.copy2(Path(root_p) / fname, dst_covers / rel_dir / fname)
            except BackupError:
                raise
            except Exception as exc:
                if has_original_db and safety_backup_path.exists():
                    try:
                        shutil.copy2(safety_backup_path, dst_db)
                    except Exception:
                        pass
                temp_target.unlink(missing_ok=True)
                raise BackupError(f"Falha durante a restauração do snapshot: {exc}") from exc

        return {
            "status": "success",
            "restored_from": str(src),
            "target_path": str(dst_db),
            "target_covers_dir": str(dst_covers),
            "safety_backup": str(safety_backup_path) if has_original_db and safety_backup_path.exists() else None,
            "size_bytes": dst_db.stat().st_size,
            "schema_version": validation["schema_version"],
            "verified_integrity": True,
            "restored_covers_count": staged_covers_count,
            "invalidated_sessions_count": invalidated_sessions_count,
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
                    covers_dir = config.BACKUPS_DIR / f"{b_path.stem}_covers"
                    if covers_dir.exists() and covers_dir.is_dir():
                        shutil.rmtree(covers_dir, ignore_errors=True)
                    b_path.unlink()
                    removed_count += 1
                except Exception:
                    pass
        return removed_count


