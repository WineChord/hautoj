#!/usr/bin/env python3
"""Mirror problem-statement images into the documentation tree.

The public corpus is always read.  An optional source corpus can be supplied to
recover image references (notably data URIs) that were intentionally omitted
from the public JSON.  Every reference receives a manifest entry, even when the
image is unavailable.

Only PNG, JPEG, GIF, and WebP payloads are accepted.  Remote requests are
limited to public HTTP(S) destinations, redirects are checked independently,
and both per-image and aggregate byte limits are enforced.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes, urljoin, urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_CORPUS = REPO_ROOT / "data" / "corpus.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "assets" / "problem-images"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "image_manifest.json"
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 12.0
MAX_REDIRECTS = 5
READ_CHUNK_BYTES = 64 * 1024
DATA_URI_MARKER = "data:image/"
BASE64_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
SAFE_PID_RE = re.compile(r"[^A-Za-z0-9_-]+")


class ImageUnavailable(Exception):
    """A reference that cannot safely produce a supported image."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ImageType:
    extension: str
    media_type: str


@dataclass
class Reference:
    problem_id: str
    image_index: int
    value: Any
    corpus_sources: list[str]


@dataclass
class DownloadResult:
    payload: bytes
    final_url: str
    redirects: int


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP destination is a pre-validated IP address."""

    def __init__(
        self,
        host: str,
        port: int,
        connect_address: str,
        timeout: float,
    ):
        super().__init__(
            host,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self.connect_address = connect_address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.connect_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--public-corpus",
        type=Path,
        default=DEFAULT_PUBLIC_CORPUS,
        help="Public corpus JSON (default: data/corpus.json).",
    )
    parser.add_argument(
        "--source-corpus",
        type=Path,
        help="Optional fuller source corpus used to recover omitted image references.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Generated image directory.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Generated image manifest.",
    )
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=DEFAULT_MAX_IMAGE_BYTES,
        help="Maximum decoded/downloaded bytes per image.",
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
        help="Maximum total bytes written for all mirrored images.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Socket timeout in seconds for each request.",
    )
    return parser.parse_args()


def load_corpus(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read corpus: {path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("problems"), dict):
        raise SystemExit(f"Corpus has no problem map: {path.name}")
    return data


def problem_sort_key(problem_id: str) -> tuple[int, int | str]:
    if problem_id.isdigit():
        return (0, int(problem_id))
    return (1, problem_id)


def merge_references(
    source_corpus: dict[str, Any] | None,
    public_corpus: dict[str, Any],
) -> list[Reference]:
    """Merge exact references while retaining stable source-corpus ordering."""

    source_problems = source_corpus.get("problems", {}) if source_corpus else {}
    public_problems = public_corpus["problems"]
    problem_ids = sorted(
        set(source_problems) | set(public_problems),
        key=lambda item: problem_sort_key(str(item)),
    )
    merged: list[Reference] = []
    for raw_problem_id in problem_ids:
        problem_id = str(raw_problem_id)
        ordered: list[tuple[Any, list[str]]] = []
        exact_positions: dict[tuple[str, str], int] = {}
        for source_name, problems in (
            ("source", source_problems),
            ("public", public_problems),
        ):
            problem = problems.get(raw_problem_id, problems.get(problem_id, {}))
            images = problem.get("images", []) if isinstance(problem, dict) else []
            if not isinstance(images, list):
                images = [images]
            for value in images:
                try:
                    identity = (
                        type(value).__name__,
                        json.dumps(value, ensure_ascii=False, sort_keys=True),
                    )
                except TypeError:
                    identity = (type(value).__name__, repr(value))
                if identity in exact_positions:
                    names = ordered[exact_positions[identity]][1]
                    if source_name not in names:
                        names.append(source_name)
                    continue
                exact_positions[identity] = len(ordered)
                ordered.append((value, [source_name]))
        for image_index, (value, sources) in enumerate(ordered, start=1):
            merged.append(
                Reference(
                    problem_id=problem_id,
                    image_index=image_index,
                    value=value,
                    corpus_sources=sources,
                )
            )
    return merged


def detect_image_type(payload: bytes) -> ImageType | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageType("png", "image/png")
    if payload.startswith(b"\xff\xd8\xff"):
        return ImageType("jpg", "image/jpeg")
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ImageType("gif", "image/gif")
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ImageType("webp", "image/webp")
    return None


def extract_data_uri(value: str) -> tuple[str, str] | None:
    marker_index = value.lower().find(DATA_URI_MARKER)
    if marker_index < 0:
        return None
    candidate = value[marker_index:]
    header, separator, payload = candidate.partition(",")
    if not separator:
        raise ImageUnavailable("data_uri_missing_payload")
    return header, payload


def decode_data_uri(value: str, max_image_bytes: int) -> bytes:
    extracted = extract_data_uri(value)
    if extracted is None:
        raise ImageUnavailable("not_a_data_uri")
    _, raw_payload = extracted
    try:
        payload_bytes = unquote_to_bytes(raw_payload)
    except Exception as exc:
        raise ImageUnavailable("data_uri_percent_decode_failed") from exc
    if len(payload_bytes) > ((max_image_bytes + 2) // 3) * 4 + 16_384:
        raise ImageUnavailable("image_size_limit_exceeded")

    compact = b"".join(payload_bytes.split())
    try:
        payload_text = compact.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ImageUnavailable("data_uri_non_ascii_payload") from exc
    if not BASE64_PAYLOAD_RE.fullmatch(payload_text):
        raise ImageUnavailable("data_uri_invalid_base64")
    payload_text = payload_text.replace("-", "+").replace("_", "/")
    padding_needed = (-len(payload_text)) % 4
    if len(payload_text.rstrip("=")) % 4 == 1:
        raise ImageUnavailable("data_uri_invalid_base64_length")
    payload_text += "=" * padding_needed
    try:
        decoded = base64.b64decode(payload_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageUnavailable("data_uri_decode_failed") from exc
    if len(decoded) > max_image_bytes:
        raise ImageUnavailable("image_size_limit_exceeded")
    return decoded


def normalized_public_url(url: str) -> tuple[str, str, int]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ImageUnavailable("invalid_url") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ImageUnavailable("unsupported_url_scheme")
    if not parts.hostname:
        raise ImageUnavailable("missing_url_host")
    if parts.username is not None or parts.password is not None:
        raise ImageUnavailable("url_credentials_rejected")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise ImageUnavailable("invalid_url_port")
    try:
        host = parts.hostname.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as exc:
        raise ImageUnavailable("invalid_url_host") from exc
    if not host:
        raise ImageUnavailable("missing_url_host")
    netloc = host
    if ":" in host:
        netloc = f"[{host}]"
    if port != (443 if scheme == "https" else 80):
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    normalized = urlunsplit((scheme, netloc, path, parts.query, ""))
    return normalized, host, port


def resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ImageUnavailable("dns_resolution_failed") from exc
    addresses: list[str] = []
    for record in records:
        address = record[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ImageUnavailable("invalid_resolved_address") from exc
        if not parsed.is_global:
            raise ImageUnavailable("private_or_special_address")
        canonical = str(parsed)
        if canonical not in addresses:
            addresses.append(canonical)
    if not addresses:
        raise ImageUnavailable("dns_resolution_failed")
    return addresses


def request_once(
    url: str,
    timeout: float,
    max_image_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    normalized, host, port = normalized_public_url(url)
    addresses = resolve_public_addresses(host, port)
    addresses.sort(key=lambda address: ipaddress.ip_address(address).version)
    parts = urlsplit(normalized)
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    connect_address = addresses[0]
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = PinnedHTTPSConnection(
            host,
            port,
            connect_address,
            timeout,
        )
    else:
        connection = http.client.HTTPConnection(connect_address, port, timeout=timeout)
    headers = {
        "Accept": "image/png,image/jpeg,image/gif,image/webp,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Host": parts.netloc,
        "User-Agent": "hautoj-image-mirror/1.0",
    }
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status != 200:
            return response.status, response_headers, b""
        content_length = response_headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_image_bytes:
                    raise ImageUnavailable("image_size_limit_exceeded")
            except ValueError:
                pass
        body = bytearray()
        while True:
            chunk = response.read(min(READ_CHUNK_BYTES, max_image_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_image_bytes:
                raise ImageUnavailable("image_size_limit_exceeded")
        return response.status, response_headers, bytes(body)
    except (TimeoutError, socket.timeout) as exc:
        raise ImageUnavailable("request_timeout") from exc
    except ssl.SSLError as exc:
        raise ImageUnavailable("tls_error") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise ImageUnavailable("request_failed") from exc
    finally:
        connection.close()


def download_image(
    source_url: str,
    timeout: float,
    max_image_bytes: int,
) -> DownloadResult:
    current_url = source_url
    visited: set[str] = set()
    for redirect_count in range(MAX_REDIRECTS + 1):
        normalized, _, _ = normalized_public_url(current_url)
        if normalized in visited:
            raise ImageUnavailable("redirect_loop")
        visited.add(normalized)
        status, headers, payload = request_once(normalized, timeout, max_image_bytes)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise ImageUnavailable("redirect_without_location")
            if redirect_count >= MAX_REDIRECTS:
                raise ImageUnavailable("too_many_redirects")
            current_url = urljoin(normalized, location)
            normalized_public_url(current_url)
            continue
        if status != 200:
            raise ImageUnavailable(f"http_status_{status}")
        return DownloadResult(payload, normalized, redirect_count)
    raise ImageUnavailable("too_many_redirects")


def safe_source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if extract_data_uri(value) is not None:
            return None
        parts = urlsplit(value)
    except (ValueError, ImageUnavailable):
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = parts.hostname
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    return value


def source_fingerprint(value: Any) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        serialized = repr(value).encode("utf-8", errors="replace")
    return hashlib.sha256(serialized).hexdigest()


def filename_for(
    problem_id: str,
    image_index: int,
    digest: str,
    extension: str,
) -> str:
    safe_problem_id = SAFE_PID_RE.sub("-", problem_id).strip("-_")
    if not safe_problem_id:
        safe_problem_id = hashlib.sha256(problem_id.encode("utf-8")).hexdigest()[:12]
    return f"p{safe_problem_id}-{image_index:02d}-{digest[:12]}.{extension}"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def replace_generated_directory(staging: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.parent / f".{destination.name}.previous"
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def mirror_references(
    references: list[Reference],
    output_dir: Path,
    max_image_bytes: int,
    max_total_bytes: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], int]:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        for reference in references:
            value = reference.value
            fingerprint = source_fingerprint(value)
            source_url = safe_source_url(value)
            entry: dict[str, Any] = {
                "problem_id": reference.problem_id,
                "image_index": reference.image_index,
                "corpus_sources": reference.corpus_sources,
                "source_type": "unknown",
                "source_url": source_url,
                "source_sha256": fingerprint,
                "status": "unavailable",
            }
            try:
                if not isinstance(value, str):
                    raise ImageUnavailable("non_string_reference")
                data_uri = extract_data_uri(value)
                if data_uri is not None:
                    entry["source_type"] = (
                        "data_uri"
                        if value.lower().startswith(DATA_URI_MARKER)
                        else "embedded_data_uri"
                    )
                    payload = decode_data_uri(value, max_image_bytes)
                    final_url = None
                    redirects = 0
                else:
                    entry["source_type"] = "url"
                    if not value.lower().startswith(("http://", "https://")):
                        raise ImageUnavailable("unsupported_reference")
                    result = download_image(value, timeout, max_image_bytes)
                    payload = result.payload
                    final_url = result.final_url
                    redirects = result.redirects
                image_type = detect_image_type(payload)
                if image_type is None:
                    raise ImageUnavailable("unsupported_or_invalid_image")
                if total_bytes + len(payload) > max_total_bytes:
                    raise ImageUnavailable("total_size_limit_exceeded")
                digest = hashlib.sha256(payload).hexdigest()
                filename = filename_for(
                    reference.problem_id,
                    reference.image_index,
                    digest,
                    image_type.extension,
                )
                destination = staging / filename
                destination.write_bytes(payload)
                total_bytes += len(payload)
                entry.update(
                    {
                        "status": "available",
                        "path": f"assets/problem-images/{filename}",
                        "media_type": image_type.media_type,
                        "bytes": len(payload),
                        "sha256": digest,
                    }
                )
                if final_url and final_url != source_url:
                    entry["final_url"] = final_url
                if redirects:
                    entry["redirects"] = redirects
            except ImageUnavailable as exc:
                entry["reason"] = exc.reason
            entries.append(entry)
        replace_generated_directory(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return entries, total_bytes


def build_manifest(
    public_corpus: dict[str, Any],
    references: list[Reference],
    entries: list[dict[str, Any]],
    total_bytes: int,
    max_image_bytes: int,
    max_total_bytes: int,
) -> dict[str, Any]:
    status_counts = Counter(entry["status"] for entry in entries)
    reason_counts = Counter(
        entry["reason"] for entry in entries if entry["status"] == "unavailable"
    )
    return {
        "schema_version": 1,
        "verified_through": public_corpus.get("verified_through"),
        "limits": {
            "max_image_bytes": max_image_bytes,
            "max_total_bytes": max_total_bytes,
            "max_redirects": MAX_REDIRECTS,
        },
        "summary": {
            "references": len(references),
            "available": status_counts["available"],
            "unavailable": status_counts["unavailable"],
            "mirrored_bytes": total_bytes,
            "unavailable_reasons": dict(sorted(reason_counts.items())),
        },
        "images": entries,
    }


def main() -> int:
    args = parse_args()
    if args.max_image_bytes <= 0 or args.max_total_bytes <= 0:
        raise SystemExit("Image and total byte limits must be positive.")
    if args.timeout <= 0:
        raise SystemExit("Timeout must be positive.")
    public_corpus = load_corpus(args.public_corpus)
    source_corpus = load_corpus(args.source_corpus) if args.source_corpus else None
    references = merge_references(source_corpus, public_corpus)
    entries, total_bytes = mirror_references(
        references,
        args.output_dir,
        args.max_image_bytes,
        args.max_total_bytes,
        args.timeout,
    )
    manifest = build_manifest(
        public_corpus,
        references,
        entries,
        total_bytes,
        args.max_image_bytes,
        args.max_total_bytes,
    )
    atomic_write_json(args.manifest, manifest)
    summary = manifest["summary"]
    print(
        "problem images: "
        f"{summary['available']} available, "
        f"{summary['unavailable']} unavailable, "
        f"{summary['mirrored_bytes']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
