#!/usr/bin/env python3
"""
Launcher POSIX seguro para execução de subprocessos com limites de recursos (RLIMIT_AS e RLIMIT_CPU)
sem usar `preexec_fn` no processo pai multithread (evita risco de deadlock entre fork e exec).

Uso:
  python scripts/bounded_exec.py --mem-mb 384 --cpu-s 185 -- /usr/bin/qpdf --linearize ...
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> int:
    mem_mb = 0
    cpu_s = 0
    i = 1
    cmd_start = -1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            cmd_start = i + 1
            break
        if arg == "--mem-mb" and i + 1 < len(argv):
            mem_mb = max(0, int(argv[i + 1]))
            i += 2
            continue
        if arg == "--cpu-s" and i + 1 < len(argv):
            cpu_s = max(0, int(argv[i + 1]))
            i += 2
            continue
        i += 1

    if cmd_start < 0 or cmd_start >= len(argv):
        sys.stderr.write("bounded_exec: missing command after --\n")
        return 125

    target_argv = argv[cmd_start:]
    target_bin = target_argv[0]

    if os.name == "posix":
        try:
            import resource

            if cpu_s > 0:
                soft_cur, hard_cur = resource.getrlimit(resource.RLIMIT_CPU)
                eff = cpu_s if hard_cur == resource.RLIM_INFINITY else min(cpu_s, hard_cur)
                resource.setrlimit(resource.RLIMIT_CPU, (eff, eff))
            if mem_mb > 0:
                mem_bytes = mem_mb * 1024 * 1024
                soft_cur, hard_cur = resource.getrlimit(resource.RLIMIT_AS)
                eff = mem_bytes if hard_cur == resource.RLIM_INFINITY else min(mem_bytes, hard_cur)
                resource.setrlimit(resource.RLIMIT_AS, (eff, eff))
        except Exception as exc:
            sys.stderr.write(f"bounded_exec: warning applying rlimits: {exc}\n")

    os.execv(target_bin, target_argv)
    return 127


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
