"""Small helpers shared by the modules that keep things in the database."""

import json
import secrets
import sqlite3
import time
import unicodedata

from .errors import BadRequest, Conflict, NotFound


def new_id(prefix: str) -> str:
    """A random identifier that is valid as a unit instance and a file name."""
    return prefix + secrets.token_hex(6)


def now() -> float:
    return time.time()


def check_name(value: str, what: str = "name") -> str:
    name = value.strip()
    if not 1 <= len(name) <= 80 or any(unicodedata.category(ch)[0] == "C" for ch in name):
        raise BadRequest(f"{what} must be 1 to 80 printable characters")
    return name


def fetch(conn: sqlite3.Connection, table: str, row_id: str | None, what: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()  # noqa: S608
    if row is None:
        raise NotFound(f"no such {what}: {row_id}")
    return row


def insert(conn: sqlite3.Connection, table: str, values: dict, what: str) -> None:
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    try:
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))  # noqa: S608
    except sqlite3.IntegrityError as error:
        raise Conflict(_integrity_message(error, what)) from error


def update(conn: sqlite3.Connection, table: str, row_id: str, values: dict, what: str) -> None:
    assignments = ", ".join(f"{column} = ?" for column in values)
    try:
        conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?",  # noqa: S608
                     (*values.values(), row_id))
    except sqlite3.IntegrityError as error:
        raise Conflict(_integrity_message(error, what)) from error


def delete(conn: sqlite3.Connection, table: str, row_id: str, what: str) -> None:
    fetch(conn, table, row_id, what)
    try:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))  # noqa: S608
    except sqlite3.IntegrityError as error:
        raise Conflict(f"this {what} is still used by something else") from error


def _integrity_message(error: sqlite3.IntegrityError, what: str) -> str:
    text = str(error)
    if "UNIQUE" in text:
        article = "an" if what[:1] in "aeiou" else "a"
        return f"{article} {what} with this name already exists"
    if "FOREIGN KEY" in text:
        return f"this {what} refers to something that does not exist"
    return f"invalid {what}: {text}"


def loads(text: str, fallback: object) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return fallback
