from __future__ import annotations

import argparse
import http.client
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

from .run_web_app import DEFAULT_HOST, DEFAULT_PORT, PROGRAM_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch KR DayPilot web app with health checks.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    result = launch(args.host, args.port, open_browser=not args.no_open)
    if not result["ok"]:
        print(f"KR DayPilot 실행 실패: {result['message']}")
        return 1

    print(f"KR DayPilot 웹앱 준비 완료: {result['url']}")
    print(f"상태 점검: {result['health_url']}")
    print(f"서버 로그: {result['log_path']}")
    if result["already_running"]:
        print("이미 실행 중인 서버를 사용했습니다.")
    else:
        print(f"새 서버 PID: {result['pid']}")
    return 0


def launch(host: str, preferred_port: int, *, open_browser: bool = True) -> dict[str, object]:
    port = choose_port(host, preferred_port)
    if port is None:
        return {"ok": False, "message": "usable_port_not_found"}

    url = f"http://{host}:{port}/"
    health_url = f"http://{host}:{port}/api/health"
    log_path = PROGRAM_ROOT / "runtime" / "webapp" / "server.log"

    already_running = health_ok(host, port)
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
        "pid": pid or "",
    }


def choose_port(host: str, preferred_port: int) -> int | None:
    for port in range(preferred_port, preferred_port + 20):
        if health_ok(host, port):
            return port
        if not port_responds(host, port):
            return port
    return None


def wait_for_health(host: str, port: int, *, process: subprocess.Popen[bytes] | None, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if health_ok(host, port):
            return True
        time.sleep(0.35)
    return False


def health_ok(host: str, port: int) -> bool:
    try:
        status = http_status(host, port, "/api/health")
    except OSError:
        return False
    return status == 200


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
