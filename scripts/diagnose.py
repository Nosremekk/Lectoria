#!/usr/bin/env python3
"""
Utilitário de Diagnóstico e Homologação do Lectoria.
Verifica ambiente, dependências, permissões, integridade do SQLite (WAL),
fontes de biblioteca, porta de rede e consumo real de memória/latência.

Uso:
    python3 scripts/diagnose.py
    python3 scripts/diagnose.py --json
"""

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import sqlite3
import sys
import time
from typing import Any, Dict

# Adiciona raiz do projeto ao path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import database
from backup_service import verify_sqlite_integrity


def get_rss_mb() -> float:
    """Lê o consumo real de memória física (VmRSS) do processo atual no Linux."""
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return round(int(parts[1]) / 1024.0, 2)
        except Exception:
            pass
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1024.0, 2)


def get_host_hardware_info() -> Dict[str, Any]:
    """Coleta informações reais da máquina hospedeira atual."""
    cpu_model = platform.processor() or "Desconhecido"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "model name" in line:
                    cpu_model = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass

    mem_total_mb = 0.0
    mem_avail_mb = 0.0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    mem_total_mb = round(int(line.split()[1]) / 1024.0, 1)
                elif line.startswith("MemAvailable:"):
                    mem_avail_mb = round(int(line.split()[1]) / 1024.0, 1)
        except Exception:
            pass

    distro = platform.platform()
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip().strip('"')
                    break
        except Exception:
            pass

    return {
        "os": distro,
        "cpu_model": cpu_model,
        "ram_total_mb": mem_total_mb,
        "ram_available_mb": mem_avail_mb,
        "python_version": platform.python_version(),
    }


def check_dependencies() -> Dict[str, Any]:
    """Verifica módulos Python obrigatórios e utilitários do sistema."""
    modules = {}
    for mod in ("fastapi", "uvicorn", "pypdf", "sqlite3"):
        try:
            m = __import__(mod)
            modules[mod] = getattr(m, "__version__", getattr(m, "sqlite_version", "ok"))
        except ImportError:
            modules[mod] = None

    binaries = {
        "sqlite3_cli": shutil.which("sqlite3") is not None,
        "zenity": shutil.which("zenity") is not None,
    }
    all_ok = all(v is not None for v in modules.values())
    return {
        "ok": all_ok,
        "python_modules": modules,
        "system_binaries": binaries,
    }


def check_directories() -> Dict[str, Any]:
    """Verifica existência e permissões de leitura/escrita dos diretórios configurados."""
    config.ensure_runtime_directories()
    dirs = {
        "library_dir": config.LIBRARY_DIR,
        "data_dir": config.DATA_DIR,
        "covers_dir": config.COVERS_DIR,
        "backups_dir": config.BACKUPS_DIR,
    }
    report = {}
    all_ok = True
    for name, path in dirs.items():
        exists = path.exists() and path.is_dir()
        readable = os.access(path, os.R_OK) if exists else False
        writable = os.access(path, os.W_OK) if exists else False
        if not (exists and readable and writable):
            all_ok = False
        report[name] = {
            "path": str(path),
            "exists": exists,
            "readable": readable,
            "writable": writable,
        }
    return {"ok": all_ok, "directories": report}


def check_database() -> Dict[str, Any]:
    """Verifica integridade, modo WAL, versão de schema e contagens do SQLite."""
    database.init_db()
    db_path = config.DB_PATH
    if not db_path.exists():
        return {"ok": False, "error": f"Banco de dados não encontrado em {db_path}"}

    integrity_ok = verify_sqlite_integrity(db_path)
    conn = database.get_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode;")
    journal_mode = cursor.fetchone()[0]

    cursor.execute("SELECT MAX(version) FROM schema_migrations;")
    user_version = cursor.fetchone()[0] or 0

    counts = {}
    for table in ("books", "users", "library_sources", "collections", "highlights", "notes", "page_drawings"):
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
    counts["drawings"] = counts["page_drawings"]

    cursor.execute("SELECT id, name, path, is_active FROM library_sources ORDER BY id ASC")
    sources = []
    for row in cursor.fetchall():
        src_path = Path(row["path"])
        sources.append({
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "enabled": bool(row["is_active"]),
            "path_exists": src_path.exists() and src_path.is_dir(),
        })

    conn.close()

    ok = (
        integrity_ok
        and journal_mode.lower() == "wal"
        and user_version == database.SCHEMA_VERSION
        and all(s["path_exists"] for s in sources if s["enabled"])
    )
    return {
        "ok": ok,
        "db_path": str(db_path),
        "size_kb": round(db_path.stat().st_size / 1024.0, 1),
        "integrity_check": "ok" if integrity_ok else "failed",
        "journal_mode": journal_mode,
        "schema_version": user_version,
        "expected_schema_version": database.SCHEMA_VERSION,
        "counts": counts,
        "library_sources": sources,
    }


def check_network() -> Dict[str, Any]:
    """Verifica disponibilidade da porta configurada e IP local."""
    port = config.PORT
    host = config.HOST
    port_in_use = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            port_in_use = True

    lan_ip = "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            lan_ip = s.getsockname()[0]
    except Exception:
        pass

    return {
        "configured_host": host,
        "configured_port": port,
        "port_currently_in_use": port_in_use,
        "lan_ip": lan_ip,
        "lan_url": f"http://{lan_ip}:{port}",
    }


def run_micro_benchmark() -> Dict[str, Any]:
    """Mede latência real de consultas SQLite e consumo de memória do processo."""
    t0 = time.perf_counter()
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, author, page_count, filesize FROM books ORDER BY title ASC")
    rows = cursor.fetchall()
    conn.close()
    db_query_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "process_rss_mb": get_rss_mb(),
        "books_query_latency_ms": db_query_ms,
        "books_indexed": len(rows),
    }


def run_diagnostics() -> Dict[str, Any]:
    hw = get_host_hardware_info()
    deps = check_dependencies()
    dirs = check_directories()
    db = check_database()
    net = check_network()
    perf = run_micro_benchmark()

    overall_ok = deps["ok"] and dirs["ok"] and db["ok"]
    return {
        "status": "ok" if overall_ok else "error",
        "hardware": hw,
        "dependencies": deps,
        "directories": dirs,
        "database": db,
        "network": net,
        "performance": perf,
    }


def main():
    parser = argparse.ArgumentParser(description="Diagnóstico completo do ambiente Lectoria.")
    parser.add_argument("--json", action="store_true", help="Exibe o relatório em formato JSON")
    args = parser.parse_args()

    report = run_diagnostics()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        sys.exit(0 if report["status"] == "ok" else 1)

    print("============================================================")
    print(" Lectoria — Diagnóstico de Ambiente e Homologação")
    print("============================================================")
    hw = report["hardware"]
    print(f"[Sistema]      SO: {hw['os']}")
    print(f"               CPU: {hw['cpu_model']}")
    print(f"               RAM Total: {hw['ram_total_mb']} MB (Disponível: {hw['ram_available_mb']} MB)")
    print(f"               Python: {hw['python_version']}")

    deps = report["dependencies"]
    print(f"\n[Dependências] Status: {'OK' if deps['ok'] else 'FALHA'}")
    for mod, ver in deps["python_modules"].items():
        print(f"               • Python {mod}: {ver or 'AUSENTE'}")
    for bin_name, present in deps["system_binaries"].items():
        print(f"               • Binário {bin_name}: {'presente' if present else 'ausente (opcional)'}")

    dirs = report["directories"]
    print(f"\n[Diretórios]   Status: {'OK' if dirs['ok'] else 'FALHA'}")
    for dname, dinfo in dirs["directories"].items():
        rw = "R/W" if (dinfo["readable"] and dinfo["writable"]) else "ERRO DE PERMISSÃO"
        print(f"               • {dname:<12}: {dinfo['path']} [{rw}]")

    db = report["database"]
    print(f"\n[Banco SQLite] Status: {'OK' if db['ok'] else 'FALHA'}")
    print(f"               • Arquivo: {db['db_path']} ({db['size_kb']} KB)")
    print(f"               • Integridade: {db['integrity_check']} | Modo: {db['journal_mode'].upper()} | Schema: V{db['schema_version']}")
    c = db["counts"]
    print(f"               • Registros: {c['books']} livros, {c['users']} usuários, {c['collections']} coleções, {c['highlights']} destaques, {c['notes']} notas, {c['drawings']} desenhos")
    for src in db["library_sources"]:
        st = "OK" if src["path_exists"] else "CAMINHO INEXISTENTE"
        print(f"               • Fonte #{src['id']} ({src['name']}): {src['path']} [{st}]")

    net = report["network"]
    port_state = "EM USO (servidor ativo ou outra aplicação)" if net["port_currently_in_use"] else "LIVRE"
    print(f"\n[Rede]         Porta {net['configured_port']}: {port_state}")
    print(f"               URL na Rede Local (LAN): {net['lan_url']}")

    perf = report["performance"]
    print(f"\n[Desempenho]   Memória RSS do processo de diagnóstico: {perf['process_rss_mb']} MB")
    print(f"               Latência de listagem SQLite ({perf['books_indexed']} livros): {perf['books_query_latency_ms']} ms")
    print("============================================================")
    print(f"RESULTADO GLOBAL: {'APROVADO (OK)' if report['status'] == 'ok' else 'REQUER ATENÇÃO'}")
    print("============================================================")

    sys.exit(0 if report["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
