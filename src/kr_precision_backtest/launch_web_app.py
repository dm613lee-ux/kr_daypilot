from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

from .run_web_app import APP_BUILD_ID, DEFAULT_HOST, DEFAULT_PORT, PROGRAM_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch KR DayPilot web app with health checks.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    result = launch(args.host, args.port, open_browser=not args.no_open)
    if not result["ok"]:
        print(f"KR DayPilot launch failed: {result['message']}")
        return 1

    print(f"KR DayPilot web app ready: {result['url']}")
    print(f"Health check: {result['health_url']}")
    print(f"Server log: {result['log_path']}")
    if result["skipped_stale_ports"]:
        print(f"Skipped stale server ports: {', '.join(map(str, result['skipped_stale_ports']))}")
    if result["already_running"]:
        print("Using an already running current server.")
    else:
        print(f"New server PID: {result['pid']}")
    return 0


def launch(host: str, preferred_port: int, *, open_browser: bool = True) -> dict[str, object]:
    port, skipped_stale_ports = choose_port(host, preferred_port)
    if port is None:
        return {"ok": False, "message": "usable_port_not_found"}

    url = f"http://{host}:{port}/?v={APP_BUILD_ID}"
    health_url = f"http://{host}:{port}/api/health"
    log_path = PROGRAM_ROOT / "runtime" / "webapp" / "server.log"

    already_running = server_is_current(host, port)
    pid = None
    process: subprocess.Popen[bytes] | None = None
    if not already_running:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        command = [
            sys.executable,
            "-m",
            "kr_precision_backtest.run_web_app",
            "--host",
            host,
            "--port",
            str(port),
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0
        process = subprocess.Popen(
            command,
            cwd=PROGRAM_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        pid = process.pid
        if not wait_for_health(host, port, process=process, seconds=20):
            return {"ok": False, "message": f"health_check_failed_port_{port}", "log_path": str(log_path)}

    if open_browser:
        webbrowser.open(url)
    return {
        "ok": True,
        "url": url,
        "health_url": health_url,
        "log_path": str(log_path),
        "already_running": already_running,
        "skipped_stale_ports": skipped_stale_ports,
        "pid": pid or "",
    }


def choose_port(host: str, preferred_port: int) -> tuple[int | None, list[int]]:
    skipped_stale_ports: list[int] = []
    first_free_port: int | None = None
    for port in range(preferred_port, preferred_port + 20):
        if server_is_current(host, port):
            return port, skipped_stale_ports
        if health_ok(host, port):
            skipped_stale_ports.append(port)
            continue
        if first_free_port is None and not port_responds(host, port):
            first_free_port = port
    return first_free_port, skipped_stale_ports


def wait_for_health(host: str, port: int, *, process: subprocess.Popen[bytes] | None, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if server_is_current(host, port):
            return True
        time.sleep(0.35)
    return False


def health_ok(host: str, port: int) -> bool:
    return health_json(host, port) is not None


def server_is_current(host: str, port: int) -> bool:
    payload = health_json(host, port)
    return bool(payload and payload.get("app_build_id") == APP_BUILD_ID)


def health_json(host: str, port: int) -> dict[str, object] | None:
    connection = http.client.HTTPConnection(host, port, timeout=1.0)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            return None
        payload = json.loads(body.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    finally:
        connection.close()


def port_responds(host: str, port: int) -> bool:
    try:
        status = http_status(host, port, "/")
    except OSError:
        return False
    return 100 <= status < 600


def http_status(host: str, port: int, path: str) -> int:
    connection = http.client.HTTPConnection(host, port, timeout=1.0)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return int(response.status)
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
