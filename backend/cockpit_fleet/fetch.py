"""Downloads from URLs into the store: the job of kind "download".

It runs in the sandbox of a run, as every job that handles what comes from
elsewhere: the server, and whatever a redirect leads to, are not trusted.
The file goes to the run's directory on disk (download/file), with a
progress file the page reads (progress.json); the backend moves it into the
store when the job has ended (storage.take_download).

Only http and https, on every redirect too. The controller's own addresses
(loopback, and those of its interfaces: Cockpit listens on them) and
link-local ones (cloud metadata services among them) are refused where the
connection is made, whatever name led there: an operator may queue a
download, and must not be able to read the services of the controller
through the store. Other addresses of the local network are allowed:
mirrors live there. With a proxy, the proxy is what is connected to.

A connection that drops is taken up again from where it stopped (a Range
request), a few times; a server that ignores Range sends the whole file
again.
"""

import functools
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .errors import BadRequest

TARGET = Path("download") / "file"
PROGRESS = "progress.json"
_CHUNK = 1024 * 1024
_TIMEOUT = 60
_RETRIES = 5
_MAX_REDIRECTS = 10
# How often the progress file is written, at most.
_PROGRESS_EVERY = 0.5
USER_AGENT = "cockpit-fleet"


class Refused(OSError):
    """A connection to an address that downloads may not reach."""


class Stopped(Exception):
    """The run was canceled."""


def check_url(value: object) -> str:
    """An http or https URL with a host, no credentials in it, nothing
    unprintable: what store.fetch takes and the job opens."""
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 \
            or any(unicodedata.category(ch)[0] in "CZ" for ch in value):
        raise BadRequest("url must be an http or https URL")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BadRequest("url must be an http or https URL")
    if parts.username is not None or parts.password is not None:
        raise BadRequest("url must not carry a user or a password")
    try:
        parts.port  # noqa: B018  (raises on a port that is not a number)
    except ValueError:
        raise BadRequest("url has an invalid port") from None
    return value


def local_addresses(fib: str, inet6: str) -> frozenset[str]:
    """The addresses of this machine's interfaces, from /proc/net/fib_trie
    and /proc/net/if_inet6 as given (the run shares the machine's network)."""
    found: set[str] = set()
    lines = fib.splitlines()
    for before, line in zip(lines, lines[1:], strict=False):
        if line.strip() == "/32 host LOCAL" and "|--" in before:
            found.add(before.split("|--", 1)[1].strip())
    for line in inet6.splitlines():
        fields = line.split()
        if fields and len(fields[0]) == 32:
            try:
                found.add(str(ipaddress.IPv6Address(int(fields[0], 16))))
            except ValueError:
                continue
    return frozenset(found)


@functools.cache
def _own() -> frozenset[str]:
    def read(path: str) -> str:
        try:
            return Path(path).read_text()
        except OSError:
            return ""
    return local_addresses(read("/proc/net/fib_trie"), read("/proc/net/if_inet6"))


def refused(address: str, own: frozenset[str] | None = None) -> bool:
    """Whether a download may not connect to ADDRESS."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
            or str(ip) in (_own() if own is None else own))


class _Guarded:
    """A connection that checks where it ended up before saying a word."""

    def connect(self) -> None:
        # Before: what the name resolves to, so that an address that does not
        # answer is not waited on. After: where the connection really went,
        # should the name have resolved otherwise the second time.
        for info in socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM):  # type: ignore[attr-defined]
            if refused(str(info[4][0])):
                raise Refused(f"downloads may not connect to {info[4][0]}")
        super().connect()  # type: ignore[misc]
        peer = self.sock.getpeername()[0]  # type: ignore[attr-defined]
        if refused(peer):
            self.sock.close()  # type: ignore[attr-defined]
            raise Refused(f"downloads may not connect to {peer}")


class _HTTPConnection(_Guarded, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_Guarded, http.client.HTTPSConnection):
    pass


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme not in ("http", "https"):
            raise urllib.error.HTTPError(newurl, code, "a redirect to another scheme", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(proxy: str = "") -> urllib.request.OpenerDirector:
    """Only what a download needs: no file:, ftp: or data:, no proxy from
    the environment."""
    director = urllib.request.OpenerDirector()
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
                urllib.request.UnknownHandler(), _HTTPHandler(), _HTTPSHandler(),
                urllib.request.HTTPDefaultErrorHandler(), _Redirects(),
                urllib.request.HTTPErrorProcessor()]
    for handler in handlers:
        director.add_handler(handler)
    return director


def _total(response, received: int) -> int | None:
    """The size of the whole file, when the server says it."""
    ranged = response.headers.get("Content-Range", "")
    if response.status == 206 and "/" in ranged:
        tail = ranged.rsplit("/", 1)[1].strip()
        return int(tail) if tail.isdigit() else None
    length = response.headers.get("Content-Length", "")
    return int(length) + (received if response.status == 206 else 0) if length.isdigit() else None


def _progress(run_dir: Path, received: int, total: int | None,
              validators: dict | None = None) -> None:
    temporary = run_dir / f"{PROGRESS}.tmp"
    temporary.write_text(json.dumps({"received": received, "total": total,
                                     "updated_at": time.time(), **(validators or {})}))
    temporary.replace(run_dir / PROGRESS)


def _validators(response) -> dict:
    """What tells this version of the file from another: ETag, Last-Modified."""
    return {"etag": (response.headers.get("ETag") or "").strip()[:512],
            "last_modified": (response.headers.get("Last-Modified") or "").strip()[:128]}


def _if_range(partial: dict) -> str:
    """A validator for If-Range: a strong ETag, or a date. None: no resume."""
    etag = str(partial.get("etag", ""))
    if etag and not etag.startswith("W/"):
        return etag
    return str(partial.get("last_modified", ""))


def download(spec: dict, run_dir: Path, say: Callable[[str], None],
             stopped: Callable[[], bool] = lambda: False,
             director: urllib.request.OpenerDirector | None = None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Fetch spec["url"] into RUN_DIR/download/file; the job's result.

    With spec["etag"] or spec["last_modified"] (a check of a file the store
    has), the first request is conditional: a 304 leaves nothing and says
    unchanged. With spec["partial"] (the validators of what an earlier
    download left there), what is there is taken up with If-Range: a server
    whose file changed meanwhile sends it whole."""
    url = check_url(spec.get("url"))
    expected = str(spec.get("sha256", "")).lower()
    limit = spec.get("max_bytes")
    if not isinstance(limit, int) or limit < 0:
        raise ValueError("max_bytes must be a number of bytes")
    director = director or opener(str(spec.get("proxy", "")))
    target = run_dir / TARGET
    target.parent.mkdir(mode=0o700, exist_ok=True)
    digest = hashlib.sha256()
    received, total, failures, final = 0, None, 0, url
    partial = spec.get("partial") if isinstance(spec.get("partial"), dict) else None
    resume = _if_range(partial) if partial else ""
    if partial and resume and target.exists():
        with open(target, "rb") as kept:
            while chunk := kept.read(_CHUNK):
                digest.update(chunk)
                received += len(chunk)
        say(f"going on from {received} bytes that arrived before")
    elif target.exists():
        target.unlink()
    conditional = {}
    if spec.get("etag"):
        conditional["If-None-Match"] = str(spec["etag"])
    if spec.get("last_modified"):
        conditional["If-Modified-Since"] = str(spec["last_modified"])
    validators = dict(partial or {}) if received else {}
    last = 0.0
    say(f"downloading {url}")
    with open(target, "r+b" if received else "wb") as out:
        out.seek(received)
        while True:
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
            if received:
                headers["Range"] = f"bytes={received}-"
                if resume:
                    headers["If-Range"] = resume
            elif conditional and not failures:
                headers.update(conditional)
            try:
                # The opener has handlers for http and https only.
                with director.open(urllib.request.Request(url, headers=headers),  # noqa: S310
                                   timeout=_TIMEOUT) as response:
                    if received and response.status != 206:
                        say("the server sends the whole file again")
                        out.seek(0)
                        out.truncate()
                        digest, received = hashlib.sha256(), 0
                    # A file taken up must stay the one it was: the first answer says which.
                    if response.status != 206 or not validators:
                        validators = _validators(response)
                        resume = _if_range(validators)
                    total = _total(response, received)
                    if response.url != final:
                        final = response.url
                        say(f"redirected to {final}")
                    if total is not None and total > limit:
                        raise ValueError(f"the file is {total} bytes:"
                                         " more than the store has room for")
                    while chunk := response.read(_CHUNK):
                        if stopped():
                            raise Stopped
                        received += len(chunk)
                        if received > limit:
                            raise ValueError("the file is larger than the store has room for")
                        out.write(chunk)
                        digest.update(chunk)
                        if time.monotonic() - last > _PROGRESS_EVERY:
                            _progress(run_dir, received, total, validators)
                            last = time.monotonic()
                if total is not None and received < total:
                    raise http.client.IncompleteRead(b"", total - received)
                break
            except Stopped:
                out.flush()
                _progress(run_dir, received, total, validators)
                raise ValueError("canceled") from None
            except Refused as error:
                raise ValueError(str(error)) from None
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == 304 and not received:
                    out.close()
                    target.unlink(missing_ok=True)
                    say("not changed since it was downloaded")
                    return {"rc": 0, "unchanged": True, "url": final,
                            **{name: value for name, value in _validators(error).items()
                               if value}}
                if error.code == 416 and total is not None and received == total:
                    break
                if error.code not in (408, 429, 500, 502, 503, 504) or failures >= _RETRIES:
                    out.flush()
                    _progress(run_dir, received, total, validators)
                    raise ValueError(f"the server answered {error.code} {error.reason}") from None
                failures += 1
            except (OSError, http.client.HTTPException) as error:
                # urllib wraps what the connection raised.
                if isinstance(getattr(error, "reason", None), Refused):
                    raise ValueError(str(error.reason)) from None
                if failures >= _RETRIES:
                    out.flush()
                    _progress(run_dir, received, total, validators)
                    raise ValueError(f"the download failed: {error}") from None
                failures += 1
                say(f"interrupted at {received} bytes ({error}): trying again")
            sleep(min(2 ** failures, 30))
        out.flush()
        os.fsync(out.fileno())
    _progress(run_dir, received, total, validators)
    found = digest.hexdigest()
    if expected and found != expected:
        # Whole but not the one asked for: nothing to take up later.
        target.unlink(missing_ok=True)
        raise ValueError(f"the file has the digest {found}, not {expected}")
    say(f"{received} bytes, sha256 {found}")
    return {"rc": 0, "size": received, "sha256": found, "url": final, **validators}
