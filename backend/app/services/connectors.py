"""Read-only, one-shot HTTPS JSON connector.

Credentials are request-scoped, never persisted, and deliberately omitted from logs.
This is an integration adapter for prototyping, not a substitute for an enterprise
secret manager, outbound proxy, or connector-specific authorization review.
"""
from __future__ import annotations
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse
import pandas as pd
from .ingestion import upload_file

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 200


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_public_https_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Connector URL must be a complete HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Do not embed credentials in the URL; use the bearer token field.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local and private network destinations are blocked.")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Local and private network destinations are blocked.")
    except ValueError as exc:
        if "blocked" in str(exc):
            raise
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
        except OSError as dns_exc:
            raise ValueError("The connector host could not be resolved safely.") from dns_exc
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("The connector host resolves to a local or private network address and was blocked.")
    return host


def import_http_json(url: str, bearer_token: str = "", filename: str = "connected-source.csv") -> dict:
    host = _validate_public_https_url(url)
    headers = {"Accept": "application/json", "User-Agent": "SigmaPilot-ReadOnly-Connector/1.0"}
    if bearer_token.strip():
        headers["Authorization"] = f"Bearer {bearer_token.strip()}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("Redirects are blocked for connector security. Use the final HTTPS URL directly.") from exc
        raise ValueError(f"Connected source returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"Connected source request failed: {type(exc).__name__}.") from exc
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Connected source response exceeds the 5 MB limit.")
    if content_type and "json" not in content_type and not content_type.startswith("text/"):
        raise ValueError("Connected source did not return JSON content.")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Connected source response is not valid UTF-8 JSON.") from exc
    records = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records or not all(isinstance(row, dict) for row in records):
        raise ValueError("Expected a non-empty JSON array of objects or an object containing a 'data' array of objects.")
    if len(records) > MAX_ROWS:
        raise ValueError(f"Connected source returned more than {MAX_ROWS} rows.")
    frame = pd.DataFrame(records)
    if frame.empty or len(frame.columns) > MAX_COLUMNS:
        raise ValueError(f"Connected source must contain between 1 and {MAX_COLUMNS} columns.")
    # Normalize nested values into strings for CSV transport; no secrets are stored here.
    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
    safe_name = filename.strip() or f"{host}-export.csv"
    if not safe_name.lower().endswith(".csv"):
        safe_name += ".csv"
    result = upload_file(safe_name, frame.to_csv(index=False).encode("utf-8"))
    result["connector"] = {"type": "read_only_https_json", "host": host, "credential_persisted": False}
    return result
