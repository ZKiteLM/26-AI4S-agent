"""Start and stop the host processes used by the local development stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".local_services"
INFRA = ("postgres", "redis", "qdrant", "elasticsearch", "minio")
SERVICES = {
    "api": (
        ROOT,
        ["uv", "run", "uvicorn", "services.api.app.main:app", "--reload", "--port", "8000"],
    ),
    "worker": (
        ROOT,
        [
            "uv",
            "run",
            "celery",
            "-A",
            "services.api.app.worker.celery_app",
            "worker",
            "-l",
            "info",
            "-Q",
            "default",
            "--pool=solo",
            "--concurrency=1",
            "-n",
            "default@%h",
        ],
    ),
    "parser": (
        ROOT,
        [
            "uv",
            "run",
            "celery",
            "-A",
            "services.api.app.worker.celery_app",
            "worker",
            "-l",
            "info",
            "-Q",
            "parser",
            "--pool=solo",
            "-n",
            "parser@%h",
        ],
    ),
    "beat": (
        ROOT,
        ["uv", "run", "celery", "-A", "services.api.app.worker.celery_app", "beat", "-l", "info"],
    ),
    "web": (ROOT / "apps/web", ["npm", "run", "dev"]),
}


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "--env-file", ".env", "-f", "infra/compose.yml", *args],
        cwd=ROOT,
        check=True,
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def record_path(name: str) -> Path:
    return RUN_DIR / f"{name}.json"


def managed_pid(name: str) -> int | None:
    path = record_path(name)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        pid = int(record["pid"])
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
        ).stdout
        if alive(pid) and record["command"] in command:
            return pid
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    path.unlink(missing_ok=True)
    return None


def previous_processes() -> dict[str, list[int]]:
    """Find project processes started manually before this script was introduced."""
    found: dict[str, list[int]] = {name: [] for name in SERVICES}
    managed_groups = {pid for name in SERVICES if (pid := managed_pid(name)) is not None}
    listing = subprocess.run(
        ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True
    ).stdout
    for line in listing.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid, command = int(parts[0]), parts[1]
        if "uvicorn services.api.app.main:app" in command:
            name = "api"
        elif "celery -A services.api.app.worker.celery_app" in command:
            if " worker " in command and "-Q parser" in command:
                name = "parser"
            elif " worker " in command and "-Q default" in command:
                name = "worker"
            elif " beat " in command or command.endswith(" beat -l info"):
                name = "beat"
            else:
                continue
        elif "npm run dev" in command or "next dev" in command:
            name = "web"
        else:
            continue
        try:
            if os.getpgid(pid) in managed_groups:
                continue
        except ProcessLookupError:
            continue
        cwd = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        expected = ROOT / "apps/web" if name == "web" else ROOT
        if f"n{expected}\n" in cwd + "\n":
            found[name].append(pid)
    return found


def project_launchd_services() -> list[str]:
    if sys.platform != "darwin":
        return []
    labels = []
    for name in SERVICES:
        label = f"gui/{os.getuid()}/ai4s.{name}"
        result = subprocess.run(
            ["launchctl", "print", label], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and f"working directory = {ROOT}\n" in result.stdout:
            labels.append(label)
    return labels


def disable_project_launchd() -> None:
    labels = project_launchd_services()
    for label in labels:
        subprocess.run(["launchctl", "bootout", label], check=True)
        print(f"{label}: unloaded previous project LaunchAgent")
    if labels:
        time.sleep(1)


def launch(name: str, cwd: Path, command: list[str]) -> None:
    if managed_pid(name):
        print(f"{name}: already running")
        return
    log_path = RUN_DIR / f"{name}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record_path(name).write_text(json.dumps({"pid": process.pid, "command": " ".join(command)}))
    time.sleep(0.5)
    if process.poll() is not None:
        record_path(name).unlink(missing_ok=True)
        raise RuntimeError(f"{name} exited immediately; see {log_path}")
    print(f"{name}: started (PID {process.pid}, log {log_path})")


def stop_process(name: str) -> None:
    pid = managed_pid(name)
    if pid is None:
        print(f"{name}: no process started by this script")
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(30):
        if not alive(pid):
            break
        time.sleep(0.1)
    else:
        os.killpg(pid, signal.SIGKILL)
    record_path(name).unlink(missing_ok=True)
    print(f"{name}: stopped")


def ollama_needed() -> bool:
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    if values.get("LLM_PROVIDER", "").strip().strip("\"'") == "ollama":
        return True
    for name in ("LLM_BASE_URL", "EMBEDDING_BASE_URL", "RERANK_BASE_URL"):
        parsed = urlparse(values.get(name, "").strip().strip("\"'"))
        if parsed.hostname in ("localhost", "127.0.0.1", "host.docker.internal") and parsed.port == 11434:
            return True
    return False


def ollama_available() -> bool:
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def ensure_port_available(name: str, port: int) -> None:
    if managed_pid(name):
        return
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Port {port} is already in use. Stop the manually started {name} service first."
            )


def start() -> None:
    if not (ROOT / ".env").exists():
        raise RuntimeError("Missing .env; copy .env.example to .env and configure it first")
    for binary in ("docker", "uv", "npm"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} is not on PATH")
    disable_project_launchd()
    RUN_DIR.mkdir(exist_ok=True)
    compose("up", "-d", *INFRA)
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    if not (ROOT / "apps/web/node_modules").exists():
        subprocess.run(["npm", "ci"], cwd=ROOT / "apps/web", check=True)
    if ollama_needed() and not ollama_available():
        if not shutil.which("ollama"):
            raise RuntimeError("LLM_PROVIDER=ollama but ollama is not on PATH")
        launch("ollama", ROOT, ["ollama", "serve"])
    ensure_port_available("api", 8000)
    ensure_port_available("web", 3000)
    for name, (cwd, command) in SERVICES.items():
        launch(name, cwd, command)
    print("Web: http://localhost:3000  API: http://localhost:8000/docs")


def stop() -> None:
    disable_project_launchd()
    for name in reversed(SERVICES):
        stop_process(name)
    previous = previous_processes()
    for name, pids in previous.items():
        for pid in pids:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
                print(f"{name}: stopped previous project process (PID {pid})")
            except ProcessLookupError:
                pass
    stop_process("ollama")
    time.sleep(0.5)
    compose("stop", *INFRA)


def status() -> None:
    previous = previous_processes()
    launchd = project_launchd_services()
    for name in (*SERVICES, "ollama"):
        pid = managed_pid(name)
        label = f"running (PID {pid})" if pid else "not running"
        if previous.get(name):
            label += f"; previous project PIDs {previous[name]}"
        print(f"{name}: {label}")
    compose("ps")
    for label in launchd:
        print(f"Previous LaunchAgent loaded: {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    action = parser.parse_args().action
    try:
        if action in ("stop", "restart"):
            stop()
        if action in ("start", "restart"):
            start()
        if action == "status":
            status()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
