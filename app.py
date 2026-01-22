import os
from datetime import datetime, timezone

import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from dotenv import load_dotenv

import dash
from dash import html, dcc
from dash.dependencies import Input, Output

load_dotenv()  # loads .env locally; harmless on Render

# --- Mongo client: reuse (connection pool) ---
_mongo_client = None


def get_mongo_client(uri: str, timeout_ms: int = 15000) -> MongoClient:
    """
    Create/reuse a MongoClient with TLS + certifi CA bundle.
    Reusing the client is recommended (pooling).
    """
    global _mongo_client

    if _mongo_client is None:
        _mongo_client = MongoClient(
            uri,
            # Fixes many SSL/TLS handshake issues on hosted platforms
            tls=True,
            tlsCAFile=certifi.where(),
            # Timeouts: Render cold starts / DNS can be slow
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )

    return _mongo_client


def check_mongo_connection(timeout_ms: int = 15000) -> dict:
    """
    Returns a dict with:
      ok: bool
      message: str
      details: str
      checked_at: datetime (UTC)
    """
    checked_at = datetime.now(timezone.utc)

    # Read env at runtime (best practice for deployment platforms)
    uri = os.getenv("MONGODB_URI")

    if not uri:
        return {
            "ok": False,
            "message": "Not configured",
            "details": "Missing env var MONGODB_URI (set it in Render or your local .env).",
            "checked_at": checked_at,
        }

    try:
        client = get_mongo_client(uri, timeout_ms=timeout_ms)

        # Force a round trip
        client.admin.command("ping")

        # Optional: list a few db names (might be empty depending on user perms)
        dbs = client.list_database_names()

        return {
            "ok": True,
            "message": "Connected ✅",
            "details": f"Ping OK. Visible DBs: {', '.join(dbs[:10])}" + ("..." if len(dbs) > 10 else ""),
            "checked_at": checked_at,
        }

    except PyMongoError as e:
        return {
            "ok": False,
            "message": "Connection failed ❌",
            "details": f"{type(e).__name__}: {e}",
            "checked_at": checked_at,
        }


# --- Dash app ---
app = dash.Dash(__name__)
app.title = "MongoDB Connection Status"

# Expose server for Render / gunicorn
server = app.server

app.layout = html.Div(
    style={
        "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif",
        "maxWidth": "780px",
        "margin": "40px auto",
        "padding": "0 16px",
        "lineHeight": "1.4",
    },
    children=[
        html.H1("MongoDB Atlas Connection Status"),
        html.P("This page pings your MongoDB Atlas cluster using the MONGODB_URI environment variable."),

        html.Div(
            id="status-card",
            style={
                "border": "1px solid #ddd",
                "borderRadius": "12px",
                "padding": "16px",
                "background": "#fafafa",
            },
            children=[
                html.Div(id="status-line", style={"fontSize": "20px", "fontWeight": "600"}),
                html.Div(id="status-details", style={"marginTop": "8px", "whiteSpace": "pre-wrap"}),
                html.Div(id="status-time", style={"marginTop": "12px", "fontSize": "12px", "color": "#666"}),
            ],
        ),

        html.Div(style={"height": "16px"}),

        html.Div(
            style={"display": "flex", "gap": "12px", "alignItems": "center"},
            children=[
                html.Button(
                    "Refresh now",
                    id="refresh-btn",
                    n_clicks=0,
                    style={
                        "padding": "10px 14px",
                        "borderRadius": "10px",
                        "border": "1px solid #ccc",
                        "cursor": "pointer",
                        "background": "white",
                    },
                ),
                dcc.Interval(id="auto-refresh", interval=10_000, n_intervals=0),  # every 10 seconds
                html.Div("Auto refresh: every 10 seconds", style={"color": "#666", "fontSize": "13px"}),
            ],
        ),
    ],
)


@app.callback(
    Output("status-line", "children"),
    Output("status-details", "children"),
    Output("status-time", "children"),
    Output("status-card", "style"),
    Input("refresh-btn", "n_clicks"),
    Input("auto-refresh", "n_intervals"),
)
def update_status(_clicks, _ticks):
    result = check_mongo_connection(timeout_ms=15000)

    card_style = {
        "border": "1px solid #ddd",
        "borderRadius": "12px",
        "padding": "16px",
        "background": "#fafafa",
    }

    if result["ok"]:
        card_style["border"] = "1px solid #cce5cc"
        card_style["background"] = "#f3fff3"
    else:
        card_style["border"] = "1px solid #f2c2c2"
        card_style["background"] = "#fff5f5"

    checked_local = result["checked_at"].astimezone()
    time_text = f"Last checked: {checked_local.strftime('%Y-%m-%d %H:%M:%S %Z')}"

    return result["message"], result["details"], time_text, card_style


if __name__ == "__main__":
    # Local dev only. On Render you'll run via gunicorn typically.
    app.run(debug=True)
