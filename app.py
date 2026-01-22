import os
import ssl
import sys
import socket
import platform
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import dash
from dash import html, dcc
from dash.dependencies import Input, Output

load_dotenv()

APP_START_UTC = datetime.now(timezone.utc)

MAX_LOG_LINES = 300
LOG_LINES = []


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    LOG_LINES.append(f"[{ts}] {msg}")
    if len(LOG_LINES) > MAX_LOG_LINES:
        del LOG_LINES[: len(LOG_LINES) - MAX_LOG_LINES]


def safe_uri_summary(uri: str) -> str:
    if not uri:
        return "<missing>"
    try:
        parsed = urlparse(uri)
        scheme = parsed.scheme or "<unknown-scheme>"
        host = parsed.hostname or "<unknown-host>"
        path = parsed.path or ""
        return f"{scheme}://***:***@{host}{path}"
    except Exception:
        return "<unable to parse uri safely>"


def get_atlas_host_from_uri(uri: str) -> str | None:
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        return parsed.hostname
    except Exception:
        return None


def resolve_host_ips(host: str, port: int = 27017) -> dict:
    out = {"host": host, "ok": False, "ips": [], "error": None}
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({info[4][0] for info in infos})
        out["ips"] = ips
        out["ok"] = len(ips) > 0
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def srv_records_debug(atlas_host: str) -> dict:
    out = {"ok": False, "query": None, "records": [], "error": None}
    try:
        srv_q = f"_mongodb._tcp.{atlas_host}"
        out["query"] = srv_q

        import dns.resolver  # type: ignore

        answers = dns.resolver.resolve(srv_q, "SRV")
        records = []
        for r in answers:
            records.append(
                {
                    "priority": int(r.priority),
                    "weight": int(r.weight),
                    "port": int(r.port),
                    "target": str(r.target).rstrip("."),
                }
            )
        out["records"] = records
        out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


_MONGO_CLIENT = None


def get_mongo_client(uri: str, timeout_ms: int = 15000) -> MongoClient:
    global _MONGO_CLIENT
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = MongoClient(
            uri,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )
        log("MongoClient created (pooled)")
    return _MONGO_CLIENT


def mongo_ping_debug(uri: str, timeout_ms: int = 15000) -> dict:
    out = {"ok": False, "ping_ok": False, "timeout_ms": timeout_ms, "dbs": [], "error": None, "traceback": None}

    try:
        client = get_mongo_client(uri, timeout_ms=timeout_ms)
        client.admin.command("ping")
        out["ping_ok"] = True

        try:
            out["dbs"] = client.list_database_names()
        except Exception as e:
            out["dbs"] = [f"<list_database_names failed: {type(e).__name__}: {e}>"]

        out["ok"] = True
        return out

    except PyMongoError as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
        return out


def build_debug_text() -> str:
    now = datetime.now(timezone.utc)
    uri = os.getenv("MONGODB_URI", "")
    uri_present = bool(uri)
    uri_safe = safe_uri_summary(uri)
    atlas_host = get_atlas_host_from_uri(uri) if uri_present else None

    lines = []
    lines.append("=== MONGODB ATLAS DEBUG (Dash) ===")
    lines.append(f"now_utc:        {now.isoformat()}")
    lines.append(f"app_start_utc:  {APP_START_UTC.isoformat()}")
    lines.append("")
    lines.append("=== RUNTIME ===")
    lines.append(f"python:         {platform.python_version()}")
    lines.append(f"executable:     {sys.executable}")
    lines.append(f"platform:       {platform.platform()}")
    lines.append(f"openssl:        {ssl.OPENSSL_VERSION}")
    lines.append(f"certifi_where:  {certifi.where()}")
    lines.append("")
    lines.append("=== ENV ===")
    lines.append(f"MONGODB_URI set: {uri_present}")
    lines.append(f"MONGODB_URI safe: {uri_safe}")

    lines.append("")
    lines.append("=== RENDER ENV (if present) ===")
    for k in ["RENDER", "RENDER_SERVICE_ID", "RENDER_SERVICE_NAME", "PORT", "PYTHON_VERSION"]:
        v = os.getenv(k)
        if v is not None:
            lines.append(f"{k}: {v}")

    if not uri_present:
        lines.append("")
        lines.append("=== NEXT STEP ===")
        lines.append("Set MONGODB_URI in Render Environment variables (no quotes).")
        return "\n".join(lines)

    lines.append("")
    lines.append("=== SRV HOST ===")
    lines.append(f"atlas_host:     {atlas_host}")
    lines.append("NOTE: Atlas SRV hosts often do NOT have A/AAAA records. SRV is what matters.")

    if atlas_host:
        lines.append("")
        lines.append("=== DNS SRV RECORDS ===")
        srv = srv_records_debug(atlas_host)
        lines.append(f"srv_ok:         {srv['ok']}")
        lines.append(f"srv_query:      {srv['query']}")
        shard_hosts = []
        if srv["records"]:
            for r in srv["records"]:
                shard_hosts.append(r["target"])
                lines.append(f"  - {r['target']}:{r['port']} (prio={r['priority']} weight={r['weight']})")
        if srv["error"]:
            lines.append(f"srv_error:      {srv['error']}")

        lines.append("")
        lines.append("=== DNS CHECK FOR SHARD HOSTS (these SHOULD resolve) ===")
        if shard_hosts:
            for h in shard_hosts:
                res = resolve_host_ips(h)
                lines.append(f"{h} -> ok={res['ok']} ips={res['ips']} err={res['error']}")
        else:
            lines.append("<no shard hosts found>")

    lines.append("")
    lines.append("=== PYMONGO PING ===")
    m = mongo_ping_debug(uri, timeout_ms=15000)
    lines.append(f"mongo_ok:       {m['ok']}")
    lines.append(f"ping_ok:        {m['ping_ok']}")
    lines.append(f"timeout_ms:     {m['timeout_ms']}")
    if m["dbs"]:
        lines.append(f"dbs:            {m['dbs']}")
    if m["error"]:
        lines.append(f"mongo_error:    {m['error']}")
    if m["traceback"]:
        lines.append("")
        lines.append("--- pymongo traceback ---")
        lines.append(m["traceback"].rstrip())

    lines.append("")
    lines.append("=== ROLLING LOG ===")
    if LOG_LINES:
        lines.extend(LOG_LINES[-MAX_LOG_LINES:])
    else:
        lines.append("<no log lines yet>")

    return "\n".join(lines)


# ---------------- Dash app (minimal) ----------------

app = dash.Dash(__name__)
server = app.server

app.layout = html.Div(
    [
        html.H3("MongoDB Atlas Debug Dashboard"),
        html.Button("REFRESH", id="refresh-btn", n_clicks=0),
        dcc.Interval(id="auto-refresh", interval=15_000, n_intervals=0),
        html.Pre(
            id="debug-output",
            children="(loading...)",
            style={
                "border": "1px solid #000",
                "padding": "12px",
                "whiteSpace": "pre-wrap",
                "fontSize": "12px",
                "marginTop": "12px",
            },
        ),
    ],
    style={"padding": "12px"},
)


@app.callback(
    Output("debug-output", "children"),
    Input("refresh-btn", "n_clicks"),
    Input("auto-refresh", "n_intervals"),
)
def refresh_debug(_clicks, _ticks):
    try:
        log("refresh_debug fired")
        return build_debug_text()
    except Exception as e:
        tb = traceback.format_exc()
        return f"DEBUG CALLBACK CRASHED:\n{type(e).__name__}: {e}\n\n{tb}"


if __name__ == "__main__":
    app.run(debug=True)
