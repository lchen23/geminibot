from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.config import AppConfig

logger = logging.getLogger(__name__)

PID_FILENAME = "geminibot.pid"
LOG_FILENAME = "geminibot.log"


def main() -> None:
    parser = argparse.ArgumentParser(prog="geminibot", description="Manage GeminiBot service lifecycle.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start GeminiBot in the background.")
    start_parser.add_argument("--foreground", action="store_true", help="Run in the foreground instead of daemonizing.")

    subparsers.add_parser("stop", help="Stop the running GeminiBot process.")
    subparsers.add_parser("status", help="Show GeminiBot process status.")
    subparsers.add_parser("restart", help="Restart GeminiBot.")

    args = parser.parse_args()

    if args.command == "start":
        if args.foreground:
            run_managed_service()
            return
        _start_background()
        return
    if args.command == "stop":
        _stop_process()
        return
    if args.command == "status":
        _print_status()
        return
    if args.command == "restart":
        _stop_process()
        _start_background()
        return


def _start_background() -> None:
    config = AppConfig.load()
    runtime_dir = _runtime_dir(config)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    pid_file = runtime_dir / PID_FILENAME
    log_file = runtime_dir / LOG_FILENAME
    running_pid = _read_running_pid(pid_file)
    if running_pid is not None:
        print(f"GeminiBot is already running (pid {running_pid}).")
        return

    command = [sys.executable, "-m", "app.cli", "start", "--foreground"]
    with log_file.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.time() + 5
    while time.time() < deadline:
        running_pid = _read_running_pid(pid_file)
        if running_pid == process.pid:
            print(f"GeminiBot started in background (pid {process.pid}).")
            print(f"Log: {log_file}")
            return
        if process.poll() is not None:
            raise SystemExit(f"GeminiBot failed to start. Check log: {log_file}")
        time.sleep(0.1)

    raise SystemExit(f"GeminiBot start timed out. Check log: {log_file}")


def _stop_process() -> None:
    config = AppConfig.load()
    pid_file = _runtime_dir(config) / PID_FILENAME
    pid = _read_running_pid(pid_file)
    if pid is None:
        print("GeminiBot is not running.")
        return

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not _is_process_alive(pid):
            _remove_pid_file(pid_file)
            print(f"GeminiBot stopped (pid {pid}).")
            return
        time.sleep(0.1)

    raise SystemExit(f"Timed out waiting for GeminiBot to stop (pid {pid}).")


def _print_status() -> None:
    config = AppConfig.load()
    pid_file = _runtime_dir(config) / PID_FILENAME
    pid = _read_running_pid(pid_file)
    if pid is None:
        print("GeminiBot is not running.")
        return
    print(f"GeminiBot is running (pid {pid}).")


def _runtime_dir(config: AppConfig) -> Path:
    return config.data_root / "runtime"


def _pid_file() -> Path:
    config = AppConfig.load()
    runtime_dir = _runtime_dir(config)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / PID_FILENAME


def _write_pid_file() -> Path:
    pid_file = _pid_file()
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return pid_file


def _remove_pid_file(pid_file: Path) -> None:
    if pid_file.exists():
        pid_file.unlink()


def _read_running_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        _remove_pid_file(pid_file)
        return None
    if not _is_process_alive(pid):
        _remove_pid_file(pid_file)
        return None
    return pid


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _install_signal_handlers(pid_file: Path) -> None:
    def _handle_signal(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    atexit.register(_remove_pid_file, pid_file)


def run_managed_service() -> None:
    from app.main import run_service

    pid_file = _write_pid_file()
    _install_signal_handlers(pid_file)
    try:
        run_service()
    finally:
        _remove_pid_file(pid_file)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1:3] == ["start", "--foreground"]:
        run_managed_service()
    else:
        main()
