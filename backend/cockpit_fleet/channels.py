"""The kinds of notification channel: what each one's settings are, and how a
message is sent through it. Standard library only.

A channel's secret (a bot token, an access token, a password) is never in its
settings: it is stored with fleet-secret and reaches the delivery as a file
that PID 1 decrypted for cockpit-fleet-notify.service.
"""

import json
import re
import smtplib
import ssl
import string
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .errors import BadRequest

KINDS = ("telegram", "webhook", "smtp", "matrix")
# Whether a kind cannot work without its secret.
SECRET_REQUIRED = {"telegram": True, "webhook": False, "smtp": False, "matrix": True}
TIMEOUT = 15
_MAX_BODY = 64 * 1024
_MAX_TEMPLATE = 16 * 1024
DEFAULT_TEMPLATE = '{"text": "$text"}'

_CHAT_RE = re.compile(r"-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")
_ROOM_RE = re.compile(r"![A-Za-z0-9._=/+-]{1,128}:[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?")
_HEADER_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,252})")
_ADDRESS_RE = re.compile(r"[^@\s<>,;:\"()\[\]\\]{1,64}@[A-Za-z0-9.-]{1,253}")
# What may stand for $secret in a webhook URL: one or more segments of a path.
_URL_SECRET_RE = re.compile(r"[A-Za-z0-9._~/-]{1,512}")


@dataclass(frozen=True)
class Message:
    """What a delivery says: a title, the text, and, for a webhook, the events."""

    title: str
    text: str
    link: str
    events: list


class SendError(Exception):
    """A channel did not take a message. The text never holds the secret."""


# --- settings -------------------------------------------------------------------

def _text(settings: dict, key: str, default: str = "", limit: int = 256) -> str:
    value = settings.get(key, default)
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise BadRequest(f"{key} must be one line of at most {limit} characters")
    return value.strip()


def check_url(value: str, key: str = "url", secret: bool = False) -> str:
    """An http(s) URL with a host and no user or password in it."""
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise BadRequest(f"{key} is not a valid URL") from None
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username
            or parts.password or parts.fragment or any(char.isspace() for char in value)):
        raise BadRequest(f"{key} must be an http(s) URL with a host, and no user or password")
    if port == 0:
        raise BadRequest(f"{key} has no valid port")
    if "$secret" in value and not secret:
        raise BadRequest(f"{key} cannot hold $secret")
    return value


def _port(settings: dict, default: int) -> int:
    value = settings.get("port", default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise BadRequest("port must be an integer between 1 and 65535")
    return value


def _address(value: object, key: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value.strip()):
        raise BadRequest(f"{key}: not a mail address: {value!r}")
    return value.strip()


def check_settings(kind: str, settings: dict) -> dict:
    """The settings of a channel of a kind, checked; only the known keys are kept."""
    known = {
        "telegram": {"chat_id", "server"},
        "webhook": {"url", "method", "template", "header"},
        "smtp": {"host", "port", "security", "username", "sender", "recipients"},
        "matrix": {"homeserver", "room_id"},
    }[kind]
    unknown = sorted(set(settings) - known)
    if unknown:
        raise BadRequest(f"unknown setting for a {kind} channel: {', '.join(unknown)}")

    if kind == "telegram":
        chat = _text(settings, "chat_id", limit=40)
        if not _CHAT_RE.fullmatch(chat):
            raise BadRequest("chat_id must be the number of a chat or the @name of a channel")
        server = check_url(_text(settings, "server", "https://api.telegram.org", 512) or
                           "https://api.telegram.org", "server")
        return {"chat_id": chat, "server": server.rstrip("/")}

    if kind == "webhook":
        url = check_url(_text(settings, "url", limit=1024), secret=True)
        method = settings.get("method", "POST")
        if method not in ("POST", "PUT"):
            raise BadRequest("method must be POST or PUT")
        template = settings.get("template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE
        if not isinstance(template, str) or len(template) > _MAX_TEMPLATE:
            raise BadRequest(f"template must be JSON text of at most {_MAX_TEMPLATE} characters")
        try:
            json.loads(template)
        except ValueError as error:
            raise BadRequest(f"template is not valid JSON: {error}") from error
        header = _text(settings, "header", "Authorization", 64) or "Authorization"
        if not _HEADER_RE.fullmatch(header):
            raise BadRequest("header must be the name of an HTTP header")
        return {"url": url, "method": method, "template": template, "header": header}

    if kind == "smtp":
        host = _text(settings, "host", limit=253)
        if not _HOST_RE.fullmatch(host):
            raise BadRequest("host must be the name or address of a mail server")
        security = settings.get("security", "starttls")
        if security not in ("starttls", "tls", "none"):
            raise BadRequest("security must be starttls, tls or none")
        username = _text(settings, "username")
        if username and security == "none":
            raise BadRequest("a password is never sent without TLS: choose starttls or tls,"
                             " or no user name")
        recipients = settings.get("recipients", [])
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 20:
            raise BadRequest("recipients must be a list of 1 to 20 mail addresses")
        return {"host": host, "port": _port(settings, 465 if security == "tls" else 587),
                "security": security, "username": username,
                "sender": _address(settings.get("sender"), "sender"),
                "recipients": [_address(value, "recipients") for value in recipients]}

    homeserver = check_url(_text(settings, "homeserver", limit=512), "homeserver")
    room = _text(settings, "room_id", limit=400)
    if not _ROOM_RE.fullmatch(room):
        raise BadRequest("room_id must be the identifier of a room, like !abc:example.org")
    return {"homeserver": homeserver.rstrip("/"), "room_id": room}


# --- sending --------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: following it would carry a token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _mask(text: str, secret: str) -> str:
    text = text.replace(secret, "********") if len(secret) >= 4 else text
    return text[:300]


def _reason(body: bytes) -> str:
    """The short reason an API gives with an error, when it gives one."""
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode(errors="replace").strip().splitlines()[0][:200] if body.strip() else ""
    if isinstance(data, dict):
        for key in ("description", "error", "message"):
            if isinstance(data.get(key), str):
                return data[key][:200]
    return ""


def http(method: str, url: str, body: bytes, headers: dict, proxy: str, secret: str = "") -> bytes:
    """Send one request; a status other than 2xx is a SendError."""
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _NoRedirect())
    # The URL was checked to be http(s) by check_url when the channel was saved.
    request = urllib.request.Request(url, data=body, method=method,  # noqa: S310
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "cockpit-fleet", **headers})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.read(_MAX_BODY)
    except urllib.error.HTTPError as error:
        with error:
            reason = _reason(error.read(_MAX_BODY))
        raise SendError(_mask(f"HTTP {error.code}" + (f": {reason}" if reason else ""),
                              secret)) from None
    except urllib.error.URLError as error:
        raise SendError(_mask(f"cannot connect: {error.reason}", secret)) from None
    except (OSError, ValueError) as error:
        raise SendError(_mask(f"cannot connect: {error}", secret)) from None


def _substitute(value: object, fields: dict, events: list) -> object:
    """The webhook template with its placeholders filled in: inside strings
    only, so that nothing in a message can change the shape of the JSON."""
    if isinstance(value, str):
        if value == "$events":
            return events
        return string.Template(value).safe_substitute(fields)
    if isinstance(value, list):
        return [_substitute(item, fields, events) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, fields, events) for key, item in value.items()}
    return value


def webhook_body(settings: dict, message: Message) -> bytes:
    fields = {"title": message.title, "text": message.text, "link": message.link,
              "count": str(len(message.events))}
    return json.dumps(_substitute(json.loads(settings["template"]), fields, message.events),
                      ensure_ascii=False).encode()


def send(kind: str, settings: dict, secret: str, message: Message, proxy: str,
         reference: str) -> None:
    """Deliver one message. reference names it, the same for the same message
    when it is tried again, so that a server that has it already can tell."""
    if SECRET_REQUIRED[kind] and not secret:
        raise SendError("the secret of this channel is not set")

    if kind == "telegram":
        body = json.dumps({"chat_id": settings["chat_id"],
                           "text": f"{message.title}\n\n{message.text}"[:4096],
                           "disable_web_page_preview": True}).encode()
        http("POST", f"{settings['server']}/bot{secret}/sendMessage", body, {}, proxy, secret)

    elif kind == "webhook":
        url, headers = settings["url"], {}
        if "$secret" in url:
            if not _URL_SECRET_RE.fullmatch(secret):
                raise SendError("the secret cannot be put in the URL: it has characters"
                                " a path cannot hold")
            url = url.replace("$secret", secret)
        elif secret:
            headers[settings["header"]] = secret
        http(settings["method"], url, webhook_body(settings, message), headers, proxy, secret)

    elif kind == "matrix":
        room = urllib.parse.quote(settings["room_id"], safe="")
        url = (f"{settings['homeserver']}/_matrix/client/v3/rooms/{room}/send/m.room.message/"
               f"{urllib.parse.quote(reference, safe='')}")
        body = json.dumps({"msgtype": "m.text",
                           "body": f"{message.title}\n\n{message.text}"}).encode()
        http("PUT", url, body, {"Authorization": f"Bearer {secret}"}, proxy, secret)

    else:
        _smtp(settings, secret, message)


def _smtp(settings: dict, secret: str, message: Message) -> None:
    mail = EmailMessage()
    mail["Subject"] = message.title
    mail["From"] = settings["sender"]
    mail["To"] = ", ".join(settings["recipients"])
    mail["Date"] = formatdate(localtime=True)
    mail["Message-ID"] = make_msgid(domain=settings["sender"].rpartition("@")[2])
    mail.set_content(message.text)
    context = ssl.create_default_context()
    try:
        if settings["security"] == "tls":
            server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=TIMEOUT,
                                      context=context)
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=TIMEOUT)
        with server:
            if settings["security"] == "starttls":
                server.starttls(context=context)
            if settings["username"]:
                server.login(settings["username"], secret)
            server.send_message(mail)
    except smtplib.SMTPResponseException as error:
        text = error.smtp_error.decode(errors="replace") if isinstance(
            error.smtp_error, bytes) else str(error.smtp_error)
        raise SendError(_mask(f"SMTP {error.smtp_code}: {text}", secret)) from None
    except (smtplib.SMTPException, OSError) as error:
        raise SendError(_mask(f"SMTP: {error}", secret)) from None
