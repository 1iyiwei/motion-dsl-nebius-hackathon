"""Keeping the two library servers up: serve-all, doctor, and launchd/systemd services.

The private server (port 8770, behind `tailscale serve --https=443`) and the public demo server (port 8771,
behind `tailscale serve --https=8443`) both read secrets from the repo-root `.env`; nothing here writes or
prints them. Service files only name the program and the working directory, so `.env` stays the one source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DEMO_PORT = 8771
DEMO_HTTPS_PORT = 8443
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
SYSTEMD_USER = Path("~/.config/systemd/user").expanduser()


@dataclass(frozen=True)
class Server:
    name: str  # "private" | "demo"
    label: str  # launchd label / systemd unit name
    port: int
    args: tuple[str, ...]  # after `canvasmind`
    token_var: str
    sandbox: str

    def url(self) -> str:
        host = os.environ.get("CANVASMIND_PUBLIC_HOST", "")
        suffix = f":{DEMO_HTTPS_PORT}" if self.name == "demo" else ""
        return f"https://{host}{suffix}/mcp" if host else ""


SERVERS = (
    Server("private", "org.canvasmind.serve", int(os.environ.get("CANVASMIND_PORT", "8770")), ("serve",),
           "CANVASMIND_MCP_TOKEN", "canvasmind"),
    Server("demo", "org.canvasmind.serve-demo", DEMO_PORT, ("serve", "--demo"),
           "CANVASMIND_DEMO_MCP_TOKEN", "canvasmind-demo"),
)


def apply_demo_env() -> None:
    """`serve --demo`: the public demo's settings, derived from the private ones in `.env`."""
    token = os.environ.get("CANVASMIND_DEMO_MCP_TOKEN", "")
    if not token:
        raise RuntimeError("CANVASMIND_DEMO_MCP_TOKEN is not set in .env")
    os.environ["CANVASMIND_HOME"] = os.environ.get("CANVASMIND_DEMO_HOME", "~/CanvasMindDemo")
    os.environ["CANVASMIND_MCP_TOKEN"] = token
    host = os.environ.get("CANVASMIND_PUBLIC_HOST", "")
    os.environ["CANVASMIND_PUBLIC_HOST"] = f"{host}:{DEMO_HTTPS_PORT}" if host else ""
    os.environ["CANVASMIND_PORT"] = str(DEMO_PORT)
    os.environ["CANVASMIND_WATCH"] = ""


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def log_dir() -> Path:
    d = Path("~/Library/Logs/CanvasMind" if sys.platform == "darwin" else "~/.local/state/canvasmind").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _program() -> list[str]:
    exe = Path(sys.executable).parent / "canvasmind"
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "canvasmind.cli"]


def _launchd_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def service_loaded(label: str) -> bool:
    return service_state(label)[0]


def service_state(label: str) -> tuple[bool, int | None, int | None]:
    """(loaded, running pid, last exit code) of a server's launchd agent or systemd unit."""
    if sys.platform == "darwin":
        r = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
        if r.returncode != 0:
            return False, None, None
        fields = dict(re.findall(r'"(\w+)" = (-?\d+);', r.stdout))
        status = int(fields["LastExitStatus"]) if "LastExitStatus" in fields else None
        code = status >> 8 if status and status > 255 else status  # a wait status: the exit code is the high byte
        return True, int(fields["PID"]) if "PID" in fields else None, code
    if not shutil.which("systemctl"):
        return False, None, None
    r = subprocess.run(["systemctl", "--user", "show", f"{label}.service", "-p", "LoadState,MainPID,ExecMainStatus"],
                       capture_output=True, text=True)
    fields = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
    if fields.get("LoadState") != "loaded":
        return False, None, None
    pid = int(fields.get("MainPID") or 0)
    return True, pid or None, int(fields.get("ExecMainStatus") or 0)


def listener_pid(port: int) -> int | None:
    """The pid holding 127.0.0.1:<port>, if lsof can see it."""
    try:
        r = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    except OSError:
        return None
    pids = [int(x) for x in r.stdout.split()]
    return pids[0] if pids else None


def _command(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def server_state(srv: Server) -> tuple[str, str]:
    """("PASS"|"WARN"|"FAIL", detail) for one server's port and service."""
    loaded, svc_pid, last_exit = service_state(srv.label)
    pid = listener_pid(srv.port) if listening(srv.port) else None
    if pid is None and listening(srv.port):
        return "PASS", "listening"
    if pid is None:
        if loaded and svc_pid is None:
            return "FAIL", f"not listening; service loaded but not running (last exit {last_exit})"
        return "FAIL", "not listening" + (" (service starting?)" if loaded else "")
    if not loaded:
        return "PASS", f"listening (pid {pid}, no service)"
    if svc_pid == pid:
        return "PASS", f"listening (service, pid {pid})"
    why = "not running" if svc_pid is None else f"pid {svc_pid}"
    return "WARN", f"listening, but not the service: pid {pid} holds the port (service {why}, last exit {last_exit})"


def outside_listeners() -> list[tuple[Server, int, str]]:
    """Servers whose port is held by a process that isn't their service."""
    found = []
    for srv in SERVERS:
        pid = listener_pid(srv.port)
        if pid is not None and pid != service_state(srv.label)[1]:
            found.append((srv, pid, _command(pid)))
    return found


def stop_outside(found: list[tuple[Server, int, str]], wait: float = 10.0) -> list[str]:
    """SIGTERM hand-started canvasmind servers (never anything else) and wait for their ports to free up."""
    out = []
    for srv, pid, cmd in found:
        if "canvasmind" not in cmd or " serve" not in cmd:
            out.append(f"{srv.name}: pid {pid} on {srv.port} is not a canvasmind server ({cmd or '?'}); left alone")
            continue
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait
        while listener_pid(srv.port) == pid and time.monotonic() < deadline:
            time.sleep(0.3)
        out.append(f"{srv.name}: stopped pid {pid}" + ("" if listener_pid(srv.port) != pid else " (still running!)"))
    return out


def serve_all(root: Path, wait: float = 30.0) -> list[str]:
    """Start whichever server isn't listening: through its service if one is installed, else as a detached process."""
    out, started = [], []
    for srv in SERVERS:
        if listening(srv.port):
            out.append(f"{srv.name}: already listening on {srv.port}")
            continue
        if service_loaded(srv.label):
            cmd = (["launchctl", "kickstart", _launchd_target(srv.label)] if sys.platform == "darwin"
                   else ["systemctl", "--user", "restart", f"{srv.label}.service"])
            subprocess.run(cmd, check=False, capture_output=True)
            how = "service"
        else:
            log = log_dir() / f"{srv.name}.log"
            with open(log, "ab") as f:
                subprocess.Popen(_program() + list(srv.args), cwd=root, stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            how = f"detached, log {log}"
        started.append(srv)
        out.append(f"{srv.name}: starting on {srv.port} ({how})")
    deadline = time.monotonic() + wait
    for srv in started:
        while not listening(srv.port) and time.monotonic() < deadline:
            time.sleep(0.5)
        out.append(f"{srv.name}: {'up' if listening(srv.port) else f'NOT listening after {wait:.0f}s'}")
    return out


# --- health check -------------------------------------------------------------------------------------------

def _tools_list(url: str, token: str) -> tuple[bool, str]:
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=15)
    except httpx.HTTPError as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    text = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        text = next((ln[5:] for ln in text.splitlines() if ln.startswith("data:")), "{}")
    try:
        tools = json.loads(text)["result"]["tools"]
    except (ValueError, KeyError, TypeError):
        return False, "unexpected response"
    return True, f"{len(tools)} tools"


def _run(cmd: list[str], timeout: float = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, r.stdout + r.stderr


def doctor() -> list[tuple[str, str, str]]:
    """(PASS|WARN|FAIL, check, detail) for Docker, both servers (locally, as services, and through Tailscale),
    and both sandboxes."""
    checks: list[tuple[str, str, str]] = []

    def add(ok: bool, name: str, detail: str) -> None:
        checks.append(("PASS" if ok else "FAIL", name, detail))

    code, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"], 20)
    add(code == 0, "docker", out.strip().splitlines()[-1] if out.strip() else "no output")
    for srv in SERVERS:
        status, detail = server_state(srv)
        checks.append((status, f"{srv.name} server :{srv.port}", detail))
        token, url = os.environ.get(srv.token_var, ""), srv.url()
        if not token or not url:
            add(False, f"{srv.name} tools/list", f"{srv.token_var} or CANVASMIND_PUBLIC_HOST missing from .env")
        else:
            ok, detail = _tools_list(url, token)
            add(ok, f"{srv.name} tools/list", f"{url.split('://')[1].split('/')[0]}: {detail}")
        code, out = _run(["nemohermes", srv.sandbox, "status"], 90)
        phase = next((ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.strip().startswith("Phase:")), "")
        add(code == 0 and phase == "Ready", f"sandbox {srv.sandbox}", phase or out.strip()[-120:] or "no output")
    return checks


# --- launchd / systemd --------------------------------------------------------------------------------------

def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchd_plist(srv: Server, root: Path) -> str:
    args = "".join(f"\n    <string>{_xml(a)}</string>" for a in _program() + list(srv.args))
    log = _xml(str(log_dir() / f"{srv.name}.log"))
    path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{srv.label}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>WorkingDirectory</key>
  <string>{_xml(str(root))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def systemd_unit(srv: Server, root: Path) -> str:
    return f"""[Unit]
Description=CanvasMind {srv.name} library server (port {srv.port})
After=network-online.target

[Service]
WorkingDirectory={root}
ExecStart={' '.join(_program() + list(srv.args))}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def service_files(root: Path) -> dict[Path, str]:
    if sys.platform == "darwin":
        return {LAUNCH_AGENTS / f"{s.label}.plist": launchd_plist(s, root) for s in SERVERS}
    return {SYSTEMD_USER / f"{s.label}.service": systemd_unit(s, root) for s in SERVERS}


def service_install(root: Path, replace: bool = False) -> list[str]:
    """Write and load both services. With `replace`, first stop hand-started canvasmind servers holding the ports."""
    out = []
    found = outside_listeners()
    if found and replace:
        out += stop_outside(found)
    else:
        for srv, pid, _ in found:
            out.append(f"warning: pid {pid} holds port {srv.port}; the {srv.name} service retries every 10 s until "
                       f"it can bind (rerun with --replace to stop it)")
    for path, text in service_files(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        out.append(f"wrote {path}")
    for srv in SERVERS:
        if sys.platform == "darwin":
            if service_loaded(srv.label):
                subprocess.run(["launchctl", "bootout", _launchd_target(srv.label)], capture_output=True)
            code, msg = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LAUNCH_AGENTS / f"{srv.label}.plist")])
        else:
            _run(["systemctl", "--user", "daemon-reload"])
            code, msg = _run(["systemctl", "--user", "enable", "--now", f"{srv.label}.service"])
        out.append(f"{srv.label}: {'loaded' if code == 0 else 'failed: ' + msg.strip()}")
    if sys.platform != "darwin":
        out.append(f"for start at boot without a login: sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")
    return out


def service_uninstall() -> list[str]:
    out = []
    for srv, path in zip(SERVERS, service_files(Path.cwd())):
        if sys.platform == "darwin":
            if service_loaded(srv.label):
                _run(["launchctl", "bootout", _launchd_target(srv.label)])
        else:
            _run(["systemctl", "--user", "disable", "--now", f"{srv.label}.service"])
        if path.exists():
            path.unlink()
            out.append(f"removed {path}")
    return out or ["nothing installed"]
