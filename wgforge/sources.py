"""Public-source discovery, bounded retrieval, and auditable immutable source bytes.

Discovery is not permission to redistribute. Cached records are pending until an
operator supplies a concrete license and rights evidence. No paid service runs
unless the SerpAPI adapter and its explicit enable flag are both configured.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import socket
import ssl
import stat
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit


class SourceError(ValueError):
    """A source failed the network, integrity, provenance, or rights policy."""


class SourceTimeoutError(SourceError):
    """The local retrieval deadline expired, including watchdog socket closes."""


class SourceHTTPError(SourceError):
    def __init__(self, status):
        super().__init__(f"Source returned HTTP {status}")
        self.status = status


class SearchError(SourceError):
    """Only fixed diagnostic labels and HTTP status may leave the API boundary."""
    def __init__(self, code, http_status=None):
        self.diagnostic = {"code": code, "http_status": http_status}
        suffix = f" (HTTP {http_status})" if http_status is not None else ""
        super().__init__("SerpAPI request failed: " + code + suffix + "; credentials omitted")


def _search_failure(exc):
    current = exc
    for _ in range(5):
        if isinstance(current, SearchError):
            return current
        if isinstance(current, SourceHTTPError):
            status = current.status
            code = ("authentication_or_access_denied" if status in {401, 403} else
                    "quota_or_rate_limit" if status == 429 else
                    "provider_server_error" if status >= 500 else "http_error")
            return SearchError(code, status)
        if isinstance(current, (TimeoutError, SourceTimeoutError)):
            return SearchError("network_timeout")
        if isinstance(current, ssl.SSLError):
            return SearchError("tls_connection_error")
        if isinstance(current, socket.gaierror):
            return SearchError("dns_lookup_failed")
        cause = current.__cause__
        if cause is None:
            break
        current = cause
    if isinstance(current, (OSError, http.client.HTTPException)):
        return SearchError("network_connection_error")
    if isinstance(current, (json.JSONDecodeError, UnicodeError)):
        return SearchError("invalid_json_response")
    return SearchError("request_or_response_error")


def _serpapi_request(settings, path, parameters):
    key = os.environ.get(settings.get("serpapi_key_env", "SERPAPI_API_KEY"), "").strip()
    if not key:
        raise SearchError("missing_api_key")
    url = "https://serpapi.com/" + path + "?" + urlencode({**parameters, "api_key": key})
    api_settings = {**settings, "allow_public_web": False, "allowed_hosts": ["serpapi.com"],
                    "max_redirects": 0, "max_bytes": min(int(settings.get("max_bytes", 2_000_000)), 2_000_000)}
    # Synchronous searches wait for search-engine results, unlike account checks
    # and ordinary file downloads. Give them their own bounded deadline.
    if path == "search.json":
        api_settings["timeout_seconds"] = settings.get("serpapi_search_timeout_seconds", 120)
    try:
        raw, _ = _fetch_https(url, api_settings, expected_mimes={"application/json"})
        payload = json.loads(raw)
    except Exception as exc:
        raise _search_failure(exc) from None
    if not isinstance(payload, dict):
        raise SearchError("malformed_response")
    if payload.get("error"):
        raise SearchError("provider_reported_error")
    return payload


def serpapi_account_status(config):
    """Free account check; never return the echoed API key, email or account ID."""
    settings = _settings(config)
    payload = _serpapi_request({**settings, "timeout_seconds": 15}, "account.json", {})
    safe = {name: value if type(value) is int and value >= 0 else None
            for name in ("total_searches_left", "this_hour_searches", "account_rate_limit_per_hour")
            for value in [payload.get(name)]}
    safe["active"] = payload.get("account_status") == "Active"
    used, limit = safe["this_hour_searches"], safe["account_rate_limit_per_hour"]
    safe["ready"] = (safe["active"] and safe["total_searches_left"] is not None
                     and safe["total_searches_left"] > 0
                     and (used is None or limit is None or used < limit))
    return safe


_MIME_EXT = {
    "text/plain": ".txt", "text/csv": ".csv", "application/csv": ".csv",
    "application/json": ".json", "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
_REQUIRED = {"id", "path", "url", "sha256", "bytes", "license", "rights_status",
             "synthetic", "retrieved_at", "title"}
_RIGHTS = {"pending", "approved", "denied"}


def _settings(config: dict) -> dict:
    return config.get("sources", config)


def _hosts_for(settings: dict, url: str) -> list[str]:
    # Paper discovery ranges over the public web; DNS pinning and HTTPS still apply.
    if settings.get("allow_public_web") is True:
        return [urlsplit(url).hostname or ""]
    return settings.get("allowed_hosts", [])


def _bound(value: Any, default: int, maximum: int, name: str) -> int:
    value = default if value is None else value
    if type(value) is not int or not 0 < value <= maximum:
        raise SourceError(f"{name} must be positive and at most {maximum}")
    return int(value)


def _public_addresses(host: str, timeout: float) -> list[str]:
    # Daemon lookup allows a bounded caller wait even when the OS DNS resolver stalls.
    result: queue.Queue = queue.Queue(maxsize=1)
    def lookup() -> None:
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.put(exc)
    threading.Thread(target=lookup, daemon=True).start()
    try:
        answer = result.get(timeout=max(0.001, timeout))
    except queue.Empty as exc:
        raise SourceTimeoutError("DNS lookup exceeded the retrieval deadline") from exc
    if isinstance(answer, Exception):
        raise SourceError("DNS lookup failed") from answer
    addresses = list(dict.fromkeys(item[4][0] for item in answer))
    if not addresses:
        raise SourceError("DNS returned no addresses")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global or parsed.is_multicast or parsed.is_unspecified or parsed.is_reserved:
            raise SourceError("Source resolves to a non-public IP address")
    return addresses


def validate_url(url: str, allowed_hosts: list[str], *, resolve: bool = True,
                 timeout: float = 10) -> tuple[str, list[str]]:
    """Reject credentials, non-HTTPS, exotic ports, suffix matches, and private DNS."""
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or "\\" in url:
        raise SourceError("Malformed source URL")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.port not in (None, 443):
            raise SourceError("Source URLs must use HTTPS on port 443")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise SourceError("Credentials and fragments are not allowed in source URLs")
        host = host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise SourceError("Malformed source URL") from exc
    if not isinstance(allowed_hosts, list) or not allowed_hosts:
        raise SourceError("An explicit nonempty allowed_hosts list is required")
    permitted = {str(h).lower().rstrip(".") for h in allowed_hosts}
    if host not in permitted or any("*" in h or "://" in h for h in permitted):
        raise SourceError("Source host is not explicitly allowlisted")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        addresses = _public_addresses(host, timeout) if resolve else []
    else:
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise SourceError("Non-public IP literals are forbidden")
        addresses = [str(address)]
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked numeric address, while verifying TLS for the source host.

    Ambient proxy variables are deliberately ignored: a proxy can bypass address
    pinning. Use an explicitly reviewed egress integration outside this adapter if
    an environment requires a proxy; do not weaken SSRF validation.
    """
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            raw.settimeout(max(0.001, deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _fetch_https(url: str, config: dict, *, expected_mimes: set[str] | None = None) -> tuple[bytes, dict]:
    """Fetch exact, uncompressed entity bytes within one overall time/size budget."""
    settings = _settings(config)
    max_bytes = _bound(settings.get("max_bytes"), 10_000_000, 100_000_000, "max_bytes")
    timeout = _bound(settings.get("timeout_seconds"), 20, 120, "timeout_seconds")
    redirects = settings.get("max_redirects", 0)
    if type(redirects) is not int or not 0 <= redirects <= 5:
        raise SourceError("max_redirects must be an integer from 0 to 5")
    deadline = time.monotonic() + timeout
    original_url = url
    history = []
    for attempt in range(redirects + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceTimeoutError("Source retrieval exceeded deadline")
        current, addresses = validate_url(url, _hosts_for(settings, url), timeout=remaining)
        parsed = urlsplit(current)
        connection = _PinnedHTTPSConnection(parsed.hostname, addresses[0], max(0.001, deadline - time.monotonic()))
        response = None
        deadline_expired = threading.Event()
        def abort_at_deadline() -> None:
            # Header parsing can otherwise be kept alive by a slow byte trickle.
            deadline_expired.set()
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        watchdog = threading.Timer(max(0.001, deadline - time.monotonic()), abort_at_deadline)
        watchdog.daemon = True
        watchdog.start()
        try:
            connection.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""),
                               headers={"User-Agent": "WorkGenesis-Forge/1.0 (public source research)",
                                        "Accept-Encoding": "identity", "Accept": ", ".join(sorted(expected_mimes or _MIME_EXT))})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                if attempt >= redirects:
                    raise SourceError("Redirect denied by source policy")
                target = response.getheader("Location")
                if not target:
                    raise SourceError("Redirect has no Location")
                history.append(current)
                url = urljoin(current, target)
                # The next iteration checks protocol, allowlist, and all DNS answers again.
                continue
            if response.status != 200:
                raise SourceHTTPError(response.status)
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding not in {"identity", ""}:
                raise SourceError("Compressed transfer content is not accepted")
            mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            allowed = expected_mimes or set(settings.get("allowed_mime_types", list(_MIME_EXT)))
            if mime not in allowed:
                raise SourceError(f"Unapproved content type: {mime or 'missing'}")
            length = response.getheader("Content-Length")
            if length is not None:
                if not length.isdecimal() or int(length) > max_bytes:
                    raise SourceError("Source Content-Length is invalid or exceeds max_bytes")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceTimeoutError("Source retrieval exceeded deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                # read1 returns after one underlying read, so slow trickles cannot
                # reset a socket timeout indefinitely inside a large read().
                part = response.read1(min(65536, max_bytes - size + 1))
                if not part:
                    break
                size += len(part)
                if size > max_bytes:
                    raise SourceError("Source stream exceeds max_bytes")
                chunks.append(part)
            body = b"".join(chunks)
            if time.monotonic() > deadline:
                raise SourceTimeoutError("Source retrieval exceeded deadline")
            if length is not None and size != int(length):
                raise SourceError("Truncated source response")
            _validate_content(body, mime)
            return body, {"url": original_url, "final_url": current, "mime_type": mime,
                          "redirect_chain": history, "etag": response.getheader("ETag"),
                          "last_modified": response.getheader("Last-Modified"),
                          "retrieval_method": "pinned_https"}
        except (OSError, http.client.HTTPException) as exc:
            if deadline_expired.is_set() or time.monotonic() >= deadline:
                raise SourceTimeoutError("Source retrieval exceeded deadline") from exc
            raise SourceError("HTTPS source retrieval failed") from exc
        finally:
            watchdog.cancel()
            if response is not None:
                response.close()
            connection.close()
    raise SourceError("Source redirect budget exhausted")


def _validate_content(body: bytes, mime: str) -> None:
    if not body:
        raise SourceError("Empty source payload")
    prefix = body[:8192].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if mime not in {"text/html", "application/xhtml+xml"} and any(marker in prefix for marker in (b"<!doctype html", b"<html", b"<script", b"<body")):
        raise SourceError("HTML/error-page payload is not a source document")
    if mime == "application/pdf" and not body.startswith(b"%PDF-"):
        raise SourceError("PDF signature does not match content type")
    if "openxmlformats" in mime and not body.startswith(b"PK\x03\x04"):
        raise SourceError("Office document signature does not match content type")
    if mime.startswith("text/") or mime in {"application/csv", "application/json"}:
        try:
            text = body.decode("utf-8-sig")
            if "\x00" in text:
                raise SourceError("NUL byte in text source")
            if mime == "application/json":
                json.loads(text)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SourceError("Invalid UTF-8 or JSON source") from exc


def _candidate(url: str, title: str, **extra: Any) -> dict:
    return {"id": "src_" + hashlib.sha256(url.encode()).hexdigest()[:20], "url": url,
            "title": title, "license": "unknown", "rights_status": "pending",
            "rights_basis": "Public availability does not establish reuse permission.",
            "synthetic": False, "provenance_kind": "exact-public", **extra}


def discover_sources(query: str, config: dict) -> list[dict]:
    """Direct URLs or genuine SerpAPI Google results. Search results remain pending.

    SerpAPI is a metered external service. Its configured env key is read only
    after enable_paid_search is explicitly true. Tests replace the HTTP boundary.
    """
    settings = _settings(config)
    adapter = settings.get("adapter", "direct")
    limit = _bound(settings.get("max_results"), 10, 100, "max_results")
    if adapter == "direct":
        entries = settings.get("direct_urls", [query] if query.startswith("https://") else [])
        if not isinstance(entries, list):
            raise SourceError("direct_urls must be a list")
        records = []
        for entry in entries[:limit]:
            entry = {"url": entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict) or not entry.get("url"):
                raise SourceError("Direct source must have a URL")
            url, _ = validate_url(entry["url"], _hosts_for(settings, entry["url"]), resolve=False)
            records.append(_candidate(url, entry.get("title", url), **{k: v for k, v in entry.items() if k not in {"url", "title", "id"}}))
        return records
    if adapter != "serpapi":
        raise SourceError(f"Unknown source discovery adapter: {adapter}")
    if settings.get("enable_paid_search") is not True:
        raise SourceError("SerpAPI requires explicit enable_paid_search=true")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise SourceError("Search query must be 1-2000 characters")
    payload = _serpapi_request(settings, "search.json", {"engine": "google", "q": query, "num": limit})
    records, seen = [], set()
    for result in payload.get("organic_results", []):
        link = result.get("link", "")
        try:
            canonical, _ = validate_url(link, _hosts_for(settings, link), resolve=False)
        except SourceError:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        records.append(_candidate(canonical, str(result.get("title", canonical)),
                                  snippet=str(result.get("snippet", "")),
                                  discovery={"adapter": "serpapi", "query": query,
                                             "position": result.get("position")}))
        if len(records) >= limit:
            break
    return records


def fetch_public_page(url: str, config: dict) -> dict:
    """Open a source landing page; it never becomes a fabricated downloaded anchor."""
    from html.parser import HTMLParser
    class PageParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts, self.links, self.ignored = [], [], 0
        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style", "noscript"}:
                self.ignored += 1
            if tag == "a":
                href = dict(attrs).get("href")
                if href:
                    absolute = urljoin(url, href).split("#", 1)[0]
                    try:
                        valid, _ = validate_url(absolute, _hosts_for(_settings(config), absolute), resolve=False)
                        if valid not in self.links:
                            self.links.append(valid)
                    except SourceError:
                        pass
        def handle_endtag(self, tag):
            if tag in {"script", "style", "noscript"} and self.ignored:
                self.ignored -= 1
        def handle_data(self, data):
            if not self.ignored and data.strip():
                self.parts.append(data.strip())
    raw, metadata = _fetch_https(url, config, expected_mimes={"text/html", "application/xhtml+xml", "text/plain"})
    parser = PageParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return {**metadata, "text": "\n".join(parser.parts), "links": parser.links,
            "sha256": hashlib.sha256(raw).hexdigest(), "kind": "landing_page"}


def _rights(record: dict) -> None:
    if record.get("rights_status") not in _RIGHTS:
        raise SourceError("rights_status must be pending, approved, or denied")
    if record.get("rights_status") == "approved":
        if not record.get("license") or str(record["license"]).lower() in {"unknown", "public", "publicly available", "pending"}:
            raise SourceError("Approved rights require an identified license or public-domain basis")
        if not record.get("rights_basis") or not record.get("rights_evidence"):
            raise SourceError("Approved rights require rights_basis and rights_evidence")


def require_production_rights(record: dict) -> None:
    """Production gate; integrity validation alone never approves reuse rights."""
    _rights(record)
    if record["rights_status"] != "approved":
        raise SourceError("Source is not approved for production reuse")
    permissions = record.get("rights_permissions", {})
    if not isinstance(permissions, dict) or any(permissions.get(scope) is not True for scope in ("training", "redistribution", "public_access")):
        raise SourceError("Production requires explicit training, redistribution, and public_access rights")
    if record.get("synthetic") is not False or record.get("provenance_kind") != "exact-public":
        raise SourceError("Production public-source tasks require exact-public source bytes")


def download_source(record: dict, out_dir: Path, config: dict) -> dict:
    """Store immutable, content-addressed bytes. record.path is never a write target."""
    if record.get("synthetic") is True:
        raise SourceError("A public download cannot be labeled synthetic")
    source = {"license": "unknown", "rights_status": "pending", **record}
    _rights(source)
    raw, response_meta = _fetch_https(source["url"], config)
    digest = hashlib.sha256(raw).hexdigest()
    ext = _MIME_EXT[response_meta["mime_type"]]
    base = Path(out_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    raw_dir = base / "raw"
    if raw_dir.is_symlink():
        raise SourceError("Raw source directory cannot be a symlink")
    raw_dir.mkdir(exist_ok=True)
    target = raw_dir / (digest + ext)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o444)
    except FileExistsError:
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise SourceError("Existing content-addressed source was altered")
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    result = {**source, **response_meta, "id": source.get("id", "src_" + digest[:20]),
              "title": source.get("title", source["url"]), "path": target.relative_to(base).as_posix(),
              "sha256": digest, "bytes": len(raw), "synthetic": False,
              "provenance_kind": "exact-public", "retrieved_at": datetime.now(timezone.utc).isoformat()}
    verify_source_record(result, base)
    return result


def verify_source_record(record: dict, base: Path) -> None:
    """Validate provenance fields and exact bytes without granting reuse rights."""
    if not isinstance(record, dict) or _REQUIRED - record.keys():
        raise SourceError("Source record is missing required manifest fields")
    if not isinstance(record["id"], str) or not record["id"] or not isinstance(record["title"], str):
        raise SourceError("Source id and title must be strings")
    if not isinstance(record["synthetic"], bool):
        raise SourceError("synthetic must be an explicit boolean")
    kind = record.get("provenance_kind", "synthetic" if record["synthetic"] else "exact-public")
    if kind != ("synthetic" if record["synthetic"] else "exact-public"):
        raise SourceError("Synthetic and exact-public provenance cannot be interchanged")
    if not record["synthetic"]:
        try:
            parsed = urlsplit(record["url"])
            validate_url(record["url"], [parsed.hostname or ""], resolve=False)
        except (TypeError, ValueError) as exc:
            raise SourceError("Exact-public sources require a valid HTTPS origin") from exc
    try:
        stamp = datetime.fromisoformat(record["retrieved_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Missing timezone")
    except (AttributeError, TypeError, ValueError) as exc:
        raise SourceError("retrieved_at must be an ISO-8601 timestamp with timezone") from exc
    _rights(record)
    if not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
        raise SourceError("Invalid source SHA-256")
    if type(record["bytes"]) is not int or not 0 < record["bytes"] <= 100_000_000:
        raise SourceError("Invalid source byte count")
    name = record["path"]
    if not isinstance(name, str) or "\\" in name:
        raise SourceError("Source path must be a relative POSIX path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(p in {"..", "."} for p in relative.parts):
        raise SourceError("Source path escapes the manifest directory")
    root = Path(base).resolve()
    target = root.joinpath(*relative.parts)
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise SourceError("Source paths may not traverse symlinks")
    if not target.resolve().is_relative_to(root):
        raise SourceError("Source path escapes the manifest directory")
    try:
        before = target.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SourceError("Source must be a regular file without hard links")
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != record["bytes"] or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                raise SourceError("Source byte count or file type does not match manifest")
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise SourceError("Source bytes are unavailable") from exc
    if digest != record["sha256"]:
        raise SourceError("Source SHA-256 does not match manifest")
    if target.parent.name == "raw" and target.stem != record["sha256"]:
        raise SourceError("Content-addressed filename does not match source SHA-256")


def load_source_manifest(path: Path) -> dict:
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise SourceError("Source manifest must be a regular file, not a symlink or special file")
        if info.st_size > 10_000_000:
            raise SourceError("Source manifest exceeds size limit")
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceError("Invalid source manifest") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list) or not manifest["sources"]:
        raise SourceError("Source manifest requires a nonempty sources list")
    seen = set()
    for record in manifest["sources"]:
        verify_source_record(record, path.parent)
        if record["id"] in seen:
            raise SourceError("Duplicate source id in manifest")
        seen.add(record["id"])
    return manifest
