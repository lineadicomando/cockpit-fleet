#!/usr/bin/python3 -I
"""Stand-ins for the services notifications go to, for notify.sh: one HTTP
server that answers as the Telegram Bot API, a webhook and a Matrix
homeserver, and a mail server that takes anything. What each receives is one
JSON line in DIRECTORY/http.jsonl or DIRECTORY/smtp.jsonl.

    notify-sinks.py DIRECTORY HTTP_PORT [SMTP_PORT]
"""

import json
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()


def record(directory: Path, name: str, entry: dict) -> None:
    with LOCK, open(directory / name, "a") as out:
        out.write(json.dumps(entry) + "\n")


class Http(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        record(self.server.directory, "http.jsonl", {
            "port": self.server.server_port, "method": self.command, "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "body": body.decode(errors="replace")})
        answer = {"ok": True, "result": {}} if "/sendMessage" in self.path else {"event_id": "$1"}
        data = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_PUT = do_POST  # noqa: N815

    def log_message(self, *args):
        pass


class Smtp(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self):
        self.reply("220 sink ESMTP")
        mail = {"from": "", "to": []}
        while True:
            line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
            verb = line[:4].upper()
            if not line or verb == "QUIT":
                self.reply("221 bye")
                return
            if verb in ("EHLO", "HELO"):
                self.reply("250 sink")
            elif verb == "MAIL":
                mail["from"] = line.split(":", 1)[1].strip()
                self.reply("250 ok")
            elif verb == "RCPT":
                mail["to"].append(line.split(":", 1)[1].strip())
                self.reply("250 ok")
            elif verb == "DATA":
                self.reply("354 go on")
                lines = []
                while (data := self.rfile.readline().decode(errors="replace")) not in (".\r\n", ""):
                    lines.append(data)
                record(self.server.directory, "smtp.jsonl", {**mail, "data": "".join(lines)})
                mail = {"from": "", "to": []}
                self.reply("250 queued")
            else:
                self.reply("250 ok")


class SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str]) -> None:
    directory = Path(argv[1])
    http = ThreadingHTTPServer(("127.0.0.1", int(argv[2])), Http)
    http.directory = directory
    if len(argv) > 3:
        smtp = SmtpServer(("127.0.0.1", int(argv[3])), Smtp)
        smtp.directory = directory
        threading.Thread(target=smtp.serve_forever, daemon=True).start()
    http.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
