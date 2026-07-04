"""HTTP fallback when venv httpx fails with OpenSSL 3.x SSL EOF.

On this machine the project venv (Python 3.11/3.13 + OpenSSL 3.x) gets
`SSL: UNEXPECTED_EOF_WHILE_READING` from several CDNs. We try multiple
fallback transports:

1. httpx with HTTP/1.1 only (OpenSSL 3.x HTTP/2 handshake is flaky).
2. curl — mature TLS stack, available on macOS/Linux.
3. System Python + requests — different OpenSSL/LibreSSL linkage.
4. Node.js — yet another TLS stack.

If all fallbacks fail, the original exception is propagated.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Any, cast
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from loguru import logger


def _system_python() -> str:
    """Return a system Python interpreter."""
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("No system python/python3 found")


def _node() -> str | None:
    """Return a Node.js binary if available."""
    return shutil.which("node")


def _curl() -> str | None:
    """Return a curl binary if available."""
    return shutil.which("curl")


def _sleep_backoff(attempt: int, base: float = 1.0) -> None:
    """指数退避休眠."""
    time.sleep(base * (2 ** attempt))


def _run_with_system_python(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run a tiny requests script via the system Python interpreter."""
    script = """
import json, sys
try:
    import requests
except Exception as e:
    print(json.dumps({"ok": False, "error": f"requests import failed: {e}"}))
    sys.exit(0)

try:
    payload = json.load(sys.stdin)
    method = payload["method"]
    url = payload["url"]
    kwargs = payload.get("kwargs", {})
    r = requests.request(method, url, **kwargs)
    print(json.dumps({
        "ok": True,
        "status_code": r.status_code,
        "text": r.text,
        "headers": dict(r.headers),
    }))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
"""
    proc = subprocess.run(
        [_system_python(), "-c", script],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=timeout + 5,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"system-python fallback crashed: {proc.stderr.decode('utf-8', 'replace')}")
    return cast(dict[str, Any], json.loads(proc.stdout.decode("utf-8", "replace")))


def _run_with_node(url: str, timeout: float) -> dict[str, Any]:
    """Run a tiny HTTPS script via Node.js."""
    node = _node()
    if not node:
        raise RuntimeError("node not found")

    script = """
const https = require('https');
const url = require('url');

function request(target, timeoutMs, maxRedirects = 3) {
  return new Promise((resolve) => {
    if (maxRedirects < 0) {
      resolve({ ok: false, error: 'too many redirects' });
      return;
    }
    const parsed = url.parse(target);
    const options = {
      hostname: parsed.hostname,
      path: parsed.path,
      method: 'GET',
      timeout: timeoutMs,
      headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36' },
    };

    const req = https.request(options, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        let next = res.headers.location;
        if (next.startsWith('/')) {
          next = `${parsed.protocol}//${parsed.host}${next}`;
        }
        request(next, timeoutMs, maxRedirects - 1).then(resolve);
        return;
      }
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        resolve({
          ok: true,
          status_code: res.statusCode,
          text: body,
          headers: res.headers,
        });
      });
    });

    req.on('error', (e) => resolve({ ok: false, error: e.message }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'timeout' }); });
    req.end();
  });
}

const target = process.argv[1];
const timeoutMs = parseInt(process.argv[2], 10);
request(target, timeoutMs).then((r) => console.log(JSON.stringify(r)));
"""
    proc = subprocess.run(
        [node, "-e", script, "--", url, str(int(timeout * 1000))],
        capture_output=True,
        timeout=timeout + 5,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"node fallback crashed: {proc.stderr.decode('utf-8', 'replace')}")
    return cast(dict[str, Any], json.loads(proc.stdout.decode("utf-8", "replace")))


def _run_with_curl(url: str, headers: dict[str, str] | None, timeout: float) -> dict[str, Any]:
    """Run a GET request via curl.

    curl uses its own TLS stack and is often the most reliable escape hatch
    when the venv Python hits OpenSSL 3.x EOF bugs.
    """
    curl = _curl()
    if not curl:
        return {"ok": False, "error": "curl not found"}

    cmd = [
        curl,
        "-s",  # silent
        "-L",  # follow redirects
        "--max-time", str(int(timeout)),
        "--retry", "0",
        "-i",  # include headers
    ]
    if headers:
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 5,
        )
    except Exception as e:
        return {"ok": False, "error": f"curl crashed: {e}"}

    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.decode("utf-8", "replace")[:500]}

    raw = proc.stdout.decode("utf-8", "replace")
    # Split headers and body on the first blank line (handles CRLF and LF)
    parts = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
    head_part = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    header_lines = head_part.splitlines()
    status_line = header_lines[0] if header_lines else ""
    status_code = 0
    if status_line.startswith("HTTP/"):
        try:
            status_code = int(status_line.split()[1])
        except (IndexError, ValueError):
            logger.debug(f"curl fallback: unexpected status line: {status_line!r}")
    else:
        logger.debug(f"curl fallback: no HTTP status line in response: {head_part[:200]!r}")

    resp_headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip()] = v.strip()

    return {
        "ok": 100 <= status_code < 400,
        "status_code": status_code,
        "text": body,
        "headers": resp_headers,
    }


class _FallbackResponse:
    """Minimal Response shim so callers can keep using `.text`/`.json()`."""

    def __init__(self, status_code: int, text: str, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Fallback response {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )


def _is_ssl_or_transport_error(e: Exception) -> bool:
    """Return True if the exception smells like a transient TLS/transport failure.

    We intentionally do NOT treat certificate hostname mismatches or expired
    certificates as retryable; those are security errors, not the OpenSSL 3.x
    EOF bug we are trying to work around.
    """
    name = type(e).__name__
    msg = str(e).lower()
    return any(
        pattern in msg or pattern in name.lower()
        for pattern in (
            "unexpected_eof",
            "eof occurred in violation",
            "zero_return",
            "incomplete read",
            "handshake failure",
            "connecterror",
            "remoteprotocolerror",
            "server disconnected",
            "broken pipe",
            "connection reset",
            "network is unreachable",
        )
    )


def _build_url(base: str, params: dict[str, Any] | None) -> str:
    """Build full URL with query string."""
    if not params:
        return base
    parts = urlparse(base)
    query = urlencode(params)
    new_parts = parts._replace(query=parts.query + ("&" if parts.query else "") + query)
    return urlunparse(new_parts)


def _try_system_python(url: str, method: str, kwargs: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Try system-python requests fallback."""
    logger.debug(f"Trying system-python fallback for {url}")
    return _run_with_system_python(
        {"method": method, "url": url, "kwargs": kwargs},
        timeout,
    )


def _try_node(url: str, timeout: float) -> dict[str, Any]:
    """Try Node.js HTTPS fallback."""
    logger.debug(f"Trying Node.js fallback for {url}")
    return _run_with_node(url, timeout)


def _try_curl(url: str, headers: dict[str, str] | None, timeout: float) -> dict[str, Any]:
    """Try curl fallback."""
    logger.debug(f"Trying curl fallback for {url}")
    return _run_with_curl(url, headers, timeout)


def fallback_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 2,
) -> _FallbackResponse:
    """GET with httpx, falling back to curl/system-python/Node.js on transport errors.

    Args:
        retries: Number of extra attempts after the first failure. This helps
            when the OpenSSL 3.x EOF bug is intermittent.
    """
    full_url = _build_url(url, params)
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            # HTTP/2 handshakes trigger the OpenSSL 3.x EOF bug more often;
            # force HTTP/1.1 to keep the venv httpx path stable.
            with httpx.Client(
                timeout=timeout, follow_redirects=True, http1=True
            ) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return _FallbackResponse(
                    resp.status_code, resp.text, dict(resp.headers)
                )
        except Exception as e:
            last_error = e
            if not _is_ssl_or_transport_error(e):
                raise

            logger.debug(f"HTTP transport error (attempt {attempt + 1}/{retries + 1}): {e}")

            # Fallback chain: curl is usually the most reliable on macOS.
            result = _try_curl(full_url, headers, timeout)
            if result.get("ok"):
                return _FallbackResponse(
                    result["status_code"], result["text"], result["headers"]
                )

            result = _try_system_python(
                full_url,
                "GET",
                {"headers": headers, "timeout": timeout},
                timeout,
            )
            if result.get("ok"):
                return _FallbackResponse(
                    result["status_code"], result["text"], result["headers"]
                )

            if _node():
                result = _try_node(full_url, timeout)
                if result.get("ok"):
                    return _FallbackResponse(
                        result["status_code"], result["text"], result["headers"]
                    )

        if attempt < retries:
            _sleep_backoff(attempt)

    raise httpx.ConnectError(
        f"all fallbacks failed for {url}: {last_error}"
    ) from last_error


def fallback_post(
    url: str,
    *,
    json: Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> _FallbackResponse:
    """POST with httpx, falling back to system-python requests on transport errors."""
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, http1=True
        ) as client:
            resp = client.post(url, json=json, headers=headers)
            resp.raise_for_status()
            return _FallbackResponse(resp.status_code, resp.text, dict(resp.headers))
    except Exception as e:
        if not _is_ssl_or_transport_error(e):
            raise

        result = _try_system_python(
            url,
            "POST",
            {"json": json, "headers": headers, "timeout": timeout},
            timeout,
        )
        if not result.get("ok"):
            raise httpx.ConnectError(f"fallback request failed: {result.get('error')}") from e
        return _FallbackResponse(result["status_code"], result["text"], result["headers"])
