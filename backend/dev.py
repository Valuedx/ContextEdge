from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
SRC_DIR = BACKEND_ROOT / "src"
MIN_PYTHON = (3, 12)
# Every routed lane. correlation/embedding were missing for a month:
# a stock deployment finished normalization and then silently never
# built episodes or embedded chunks - the graph chain's two middle
# links had no consumer (found by external review, 2026-08-18).
DEFAULT_QUEUES = "default,sync,hydration,extraction,correlation,embedding,pattern,evaluation"


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), pythonpath])
    else:
        env["PYTHONPATH"] = str(SRC_DIR)
    return env


def _normalize_extra_args(extra_args: list[str]) -> list[str]:
    if extra_args[:1] == ["--"]:
        return extra_args[1:]
    return extra_args


def _run(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=BACKEND_ROOT,
        env=_build_env(),
        check=False,
    )
    return completed.returncode


def _ensure_python_version() -> int:
    if sys.version_info >= MIN_PYTHON:
        return 0

    required = ".".join(str(part) for part in MIN_PYTHON)
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(
        "ContextEdge host-run commands require Python "
        f"{required}+.\nCurrent interpreter: {sys.executable}\nCurrent version: {current}",
        file=sys.stderr,
    )
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ContextEdge host-development commands with backend/src on PYTHONPATH."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    api = subparsers.add_parser("api", help="Start the FastAPI dev server.")
    api.add_argument("extra_args", nargs=argparse.REMAINDER)

    seed = subparsers.add_parser("seed", help="Seed local development data.")
    seed.add_argument("extra_args", nargs=argparse.REMAINDER)

    worker = subparsers.add_parser("worker", help="Start the Celery worker.")
    worker.add_argument("extra_args", nargs=argparse.REMAINDER)

    beat = subparsers.add_parser("beat", help="Start Celery beat.")
    beat.add_argument("extra_args", nargs=argparse.REMAINDER)

    return parser


def main() -> int:
    version_error = _ensure_python_version()
    if version_error:
        return version_error

    args = _build_parser().parse_args()
    extra_args = _normalize_extra_args(getattr(args, "extra_args", []))

    commands = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "contextedge.main:app",
            "--app-dir",
            "src",
            "--reload",
            "--port",
            "8000",
            *extra_args,
        ],
        "seed": [sys.executable, "-m", "contextedge.seed", *extra_args],
        "worker": [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "contextedge.workers.celery_app",
            "worker",
            "-l",
            "INFO",
            "-Q",
            DEFAULT_QUEUES,
            # Windows default; skipped when the caller picks a pool (threads
            # works fine on Windows for I/O-bound queues — see RUNBOOK
            # "Worker topology"). Prefork remains unusable on Windows.
            *(
                ["-P", "solo"]
                if os.name == "nt"
                and not any(
                    a in ("-P", "--pool") or a.startswith("--pool=")
                    for a in extra_args
                )
                else []
            ),
            *extra_args,
        ],
        "beat": [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "contextedge.workers.celery_app",
            "beat",
            "-l",
            "INFO",
            *extra_args,
        ],
    }
    return _run(commands[args.command])


if __name__ == "__main__":
    raise SystemExit(main())
