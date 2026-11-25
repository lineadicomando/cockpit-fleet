"""Markdown to HTML for the documents, with the standard library only.

One renderer, on the backend, for the page, the preview while editing and
the exports: what the browser is given is decided here, once.

A subset of GitHub's Markdown: headings (# and underlined), paragraphs and
line breaks, emphasis, strong, strikethrough, code spans and blocks
(fenced or indented), quotes, lists ordered or not, nested, with tasks,
tables, thematic breaks, links (inline, by reference, <autolinks> and bare
http(s) addresses) and images; an optional front matter of "key: value"
lines between two lines of ---.

What it lets through is held to what a document needs:

- raw HTML is text: every character of the source reaches the page
  escaped, and the only tags are the renderer's own;
- a link goes to http(s), mailto, fleet: (an object of Fleet, named by
  kind and id), or to another file of the documents, given relative to the
  document or to the root of the tree, made fleet:doc/PATH; anything else
  (javascript:, data:, file:, a path out of the tree) is left as its text;
- an image is a file of the documents' tree, embedded in the page as a
  data: URI, within a budget: an image from elsewhere is never fetched, by
  the controller or by the browser, and shows as its text.

It is not CommonMark to the letter: what it does differently is on the side
of showing text as it was written.
"""

import base64
import bisect
import html
import posixpath
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from .errors import BadRequest
from .validators import check_file_path

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".avif": "image/avif"}
# One image embedded, at most, and all those of a document.
MAX_IMAGE = 4 * 1024 * 1024
MAX_IMAGES = 16 * 1024 * 1024
# Quotes and lists within each other: beyond, the rest is text.
_MAX_DEPTH = 16
# What emphasis looks back over for its opener, and a link for its bracket.
_EMPHASIS_REACH = 200
_FRONT_MATTER_LINES = 100
_MAX_META = 50

_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_BULLET = re.compile(r"^( {0,3})([-+*])(?:( +)(.*))?$")
_ORDERED = re.compile(r"^( {0,3})(\d{1,9})([.)])(?:( +)(.*))?$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_TABLE_DELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_REF_DEF = re.compile(r"^ {0,3}\[([^\]]{1,999})\]:[ \t]*(?:<([^>\n]*)>|(\S+))"
                      r"(?:[ \t]+(?:\"([^\"]*)\"|'([^']*)'|\(([^)]*)\)))?[ \t]*$")
_TASK = re.compile(r"^\[([ xX])\](?:[ \t]+|$)")
_META = re.compile(r"^([A-Za-z0-9_-]{1,40})[ \t]*:[ \t]*(.*)$")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")
_EMAIL = re.compile(r"<([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
                    r"[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)>")
_BARE = re.compile(r"https?://[^\s<]+")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_FLEET = re.compile(r"[a-z]{2,20}(?:/[A-Za-z0-9._~%/@:+-]*)?(?:#[A-Za-z0-9._-]*)?")
_ESCAPABLE = re.compile(r"\\([!-/:-@\[-`{-~])")
_LANGUAGE = re.compile(r"[A-Za-z0-9_+#.-]{1,40}")
_TAG = re.compile(r"<[^>]*>")


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _space(ch: str) -> bool:
    return ch.isspace()


def _punctuation(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def _label(text: str) -> str:
    """A reference's label as it is matched: case and spaces do not count."""
    return " ".join(text.split()).casefold()


def _unescape(url: str) -> str:
    return html.unescape(_ESCAPABLE.sub(r"\1", url))


def front_matter(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """The "key: value" lines between a first line of --- and the next ---
    or ..., and the lines after them; none when the text does not open so."""
    if not lines or lines[0].rstrip() != "---":
        return {}, lines
    for index in range(1, min(len(lines), _FRONT_MATTER_LINES)):
        if lines[index].rstrip() in ("---", "..."):
            meta: dict[str, str] = {}
            for line in lines[1:index]:
                found = _META.match(line)
                if found and len(meta) < _MAX_META:
                    value = found.group(2).strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    meta[found.group(1).lower()] = value[:1000]
            return meta, lines[index + 1:]
    return {}, lines


@dataclass
class Rendered:
    html: str
    meta: dict[str, str]
    title: str
    headings: list[dict] = field(default_factory=list)
    images: int = 0
    # Images not shown: from outside the tree, missing, or beyond the budget.
    refused: list[str] = field(default_factory=list)


@dataclass
class _Marker:
    ordered: bool
    char: str
    start: int
    indent: int
    content: str


def _marker(line: str) -> _Marker | None:
    if _HR.match(line):
        return None
    found = _BULLET.match(line)
    ordered = False
    if found is None:
        found = _ORDERED.match(line)
        ordered = True
        if found is None:
            return None
    lead, spaces, content = found.group(1), found.groups()[-2] or "", found.groups()[-1] or ""
    width = len(found.group(2)) + (1 if ordered else 0)
    if not content:
        indent = len(lead) + width + 1
    elif len(spaces) > 4:
        indent = len(lead) + width + 1
        content = " " * (len(spaces) - 1) + content
    else:
        indent = len(lead) + width + len(spaces)
    return _Marker(ordered, found.group(3) if ordered else found.group(2),
                   int(found.group(2)) if ordered else 1, indent, content)


def _fence(line: str):
    found = _FENCE.match(line)
    if found is None or found.group(2)[0] == "`" and "`" in found.group(3):
        return None
    return found


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class _Renderer:
    def __init__(self, base: PurePosixPath, load: Callable[[PurePosixPath], bytes | None] | None):
        self.base = base
        self.load = load
        self.refs: dict[str, tuple[str, str]] = {}
        self.ids: set[str] = set()
        self.headings: list[dict] = []
        self.spent = 0
        self.images = 0
        self.refused: list[str] = []

    # --- blocks ---------------------------------------------------------------

    def definitions(self, lines: list[str]) -> list[str]:
        """Take out the definitions of references, outside code blocks: each
        after a blank line, or after another definition, as none continues
        a paragraph."""
        kept: list[str] = []
        fence, after_blank, after_definition = None, True, False
        for line in lines:
            if fence is not None:
                kept.append(line)
                if re.match(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", line):
                    fence, after_blank = None, False
                continue
            found = _REF_DEF.match(line)
            if found and (after_blank or after_definition):
                label = _label(found.group(1))
                url = found.group(2) if found.group(2) is not None else found.group(3)
                title = next((group for group in found.groups()[3:] if group is not None), "")
                if label and label not in self.refs:
                    self.refs[label] = (_unescape(url), _unescape(title))
                after_definition = True
                continue
            opened = _fence(line)
            if opened:
                fence = opened.group(2)
            after_blank, after_definition = not line.strip(), False
            kept.append(line)
        return kept

    def _starts(self, line: str) -> bool:
        """Whether LINE begins a block that ends a paragraph."""
        if _fence(line) or _HR.match(line) or _QUOTE.match(line):
            return True
        heading = _ATX.match(line)
        if heading and (heading.group(2) is not None or line.strip() == heading.group(1)):
            return True
        marker = _marker(line)
        return marker is not None and bool(marker.content.strip()) and (
            not marker.ordered or marker.start == 1)

    def _table_at(self, lines: list[str], index: int) -> bool:
        return ("|" in lines[index] and index + 1 < len(lines)
                and "|" in lines[index + 1] and _TABLE_DELIM.match(lines[index + 1]) is not None
                and len(self._cells(lines[index])) == len(self._cells(lines[index + 1])))

    def blocks(self, lines: list[str], depth: int = 0, tight: bool = False) -> str:
        out: list[str] = []
        index, count = 0, len(lines)
        while index < count:
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            if depth > _MAX_DEPTH:
                out.append(f"<p>{_escape(chr(10).join(lines[index:]).strip())}</p>")
                break
            if _indent(line) >= 4:
                code = []
                while index < count and (not lines[index].strip()
                                         or _indent(lines[index]) >= 4):
                    code.append(lines[index][4:])
                    index += 1
                while code and not code[-1].strip():
                    code.pop()
                out.append(f"<pre><code>{_escape(chr(10).join(code))}\n</code></pre>")
                continue
            opened = _fence(line)
            if opened:
                index = self._code(lines, index, opened, out)
                continue
            heading = _ATX.match(line)
            if heading and (heading.group(2) is not None or line.strip() == heading.group(1)):
                text = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading.group(2) or "")
                out.append(self._heading(len(heading.group(1)), text))
                index += 1
                continue
            if _HR.match(line):
                out.append("<hr>")
                index += 1
                continue
            if _QUOTE.match(line):
                index = self._quote(lines, index, depth, out)
                continue
            if _marker(line):
                index = self._list(lines, index, depth, out)
                continue
            if self._table_at(lines, index):
                index = self._table(lines, index, out)
                continue
            index = self._paragraph(lines, index, tight, out)
        return "\n".join(out)

    def _code(self, lines: list[str], index: int, opened, out: list[str]) -> int:
        lead, fence = len(opened.group(1)), opened.group(2)
        words = opened.group(3).split()
        language = words[0] if words and _LANGUAGE.fullmatch(_unescape(words[0])) else ""
        close = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        body = []
        index += 1
        while index < len(lines) and not close.match(lines[index]):
            line = lines[index]
            body.append(line[min(lead, _indent(line)):])
            index += 1
        attribute = f' class="language-{_escape(_unescape(language))}"' if language else ""
        text = "\n".join(body)
        out.append(f"<pre><code{attribute}>{_escape(text)}{chr(10) if body else ''}</code></pre>")
        return index + 1

    def _quote(self, lines: list[str], index: int, depth: int, out: list[str]) -> int:
        inner: list[str] = []
        while index < len(lines):
            line = lines[index]
            found = _QUOTE.match(line)
            if found:
                inner.append(found.group(1))
            elif (line.strip() and inner and inner[-1].strip() and not self._starts(line)
                  and not _fence(inner[-1])):
                inner.append(line)  # a lazy line of a paragraph
            else:
                break
            index += 1
        out.append(f"<blockquote>\n{self.blocks(inner, depth + 1)}\n</blockquote>")
        return index

    def _list(self, lines: list[str], index: int, depth: int, out: list[str]) -> int:
        first = _marker(lines[index])
        items: list[tuple[_Marker, list[str]]] = []
        loose = False
        while index < len(lines):
            marker = _marker(lines[index])
            if (marker is None or marker.ordered != first.ordered or marker.char != first.char
                    or _indent(lines[index]) >= first.indent and items):
                break
            body = [marker.content]
            index += 1
            while index < len(lines):
                line = lines[index]
                if not line.strip():
                    body.append("")
                elif _indent(line) >= marker.indent:
                    body.append(line[marker.indent:])
                elif (body[-1].strip() and not self._starts(line) and not _marker(line)
                      and not _SETEXT.match(line)):
                    body.append(line.strip())  # a lazy line of a paragraph
                else:
                    break
                index += 1
            trailing = 0
            while body and not body[-1].strip():
                body.pop()
                trailing += 1
            if "" in body:
                loose = True
            if trailing and index < len(lines):
                following = _marker(lines[index])
                if following and following.ordered == first.ordered \
                        and following.char == first.char:
                    loose = True
            items.append((marker, body))
            if trailing and index < len(lines) and not _marker(lines[index]):
                break
        rendered = []
        for _marker_of, body in items:
            task = _TASK.match(body[0]) if body else None
            prefix, attribute = "", ""
            if task:
                body = [body[0][task.end():], *body[1:]]
                checked = " checked" if task.group(1) in "xX" else ""
                prefix = f'<input type="checkbox" disabled{checked}> '
                attribute = ' class="task"'
            content = self.blocks(body, depth + 1, tight=not loose)
            rendered.append(f"<li{attribute}>{prefix}{content}</li>")
        tag = "ol" if first.ordered else "ul"
        start = f' start="{first.start}"' if first.ordered and first.start != 1 else ""
        out.append(f"<{tag}{start}>\n" + "\n".join(rendered) + f"\n</{tag}>")
        return index

    def _cells(self, row: str) -> list[str]:
        text = row.strip()
        if text.startswith("|"):
            text = text[1:]
        if text.endswith("|") and not text.endswith("\\|"):
            text = text[:-1]
        cells, current, index, code = [], [], 0, 0
        while index < len(text):
            ch = text[index]
            if ch == "\\" and index + 1 < len(text) and text[index + 1] == "|":
                current.append("|")
                index += 2
                continue
            if ch == "`":
                run = len(text[index:]) - len(text[index:].lstrip("`"))
                code = 0 if code == run else (code or run)
                current.append("`" * run)
                index += run
                continue
            if ch == "|" and not code:
                cells.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
            index += 1
        cells.append("".join(current).strip())
        return cells

    def _table(self, lines: list[str], index: int, out: list[str]) -> int:
        header = self._cells(lines[index])
        aligns = []
        for cell in self._cells(lines[index + 1]):
            left, right = cell.startswith(":"), cell.endswith(":")
            aligns.append("center" if left and right else "right" if right
                          else "left" if left else "")

        def row(cells: list[str], tag: str) -> str:
            cells = (cells + [""] * len(header))[:len(header)]
            parts = []
            for cell, align in zip(cells, aligns, strict=True):
                style = f' style="text-align: {align}"' if align else ""
                parts.append(f"<{tag}{style}>{self.inline(cell)}</{tag}>")
            return "<tr>" + "".join(parts) + "</tr>"

        body = []
        index += 2
        while index < len(lines) and lines[index].strip() and not self._starts(lines[index]):
            body.append(row(self._cells(lines[index]), "td"))
            index += 1
        out.append("<table>\n<thead>\n" + row(header, "th") + "\n</thead>"
                   + ("\n<tbody>\n" + "\n".join(body) + "\n</tbody>" if body else "")
                   + "\n</table>")
        return index

    def _paragraph(self, lines: list[str], index: int, tight: bool, out: list[str]) -> int:
        paragraph = [lines[index]]
        index += 1
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                break
            setext = _SETEXT.match(line)
            if setext:
                text = "\n".join(part.strip() for part in paragraph)
                out.append(self._heading(1 if setext.group(1)[0] == "=" else 2, text))
                return index + 1
            if self._starts(line) or self._table_at(lines, index):
                break
            paragraph.append(line)
            index += 1
        # Trailing spaces within it are a line break: only the last line loses them.
        text = self.inline("\n".join(part.lstrip() for part in paragraph).rstrip())
        out.append(text if tight else f"<p>{text}</p>")
        return index

    def _heading(self, level: int, text: str) -> str:
        inner = self.inline(text.strip())
        plain = html.unescape(_TAG.sub("", inner))
        slug = re.sub(r"[^\w\- ]", "", plain.casefold()).strip().replace(" ", "-") or "section"
        anchor, number = slug, 1
        while anchor in self.ids:
            anchor = f"{slug}-{number}"
            number += 1
        self.ids.add(anchor)
        self.headings.append({"level": level, "text": plain, "id": anchor})
        return f'<h{level} id="{_escape(anchor)}">{inner}</h{level}>'

    # --- inlines --------------------------------------------------------------

    def inline(self, text: str, links: bool = True) -> str:
        nodes: list[list] = []
        buffer: list[str] = []
        brackets = self._brackets(text) if links else {}
        ticks = self._ticks(text)

        def flush() -> None:
            if buffer:
                nodes.append(["text", "".join(buffer)])
                buffer.clear()

        index, length = 0, len(text)
        while index < length:
            ch = text[index]
            if ch == "\\" and index + 1 < length:
                following = text[index + 1]
                if following == "\n":
                    flush()
                    nodes.append(["html", "<br>\n"])
                    index += 2
                    continue
                if _ESCAPABLE.match(text, index):
                    buffer.append(following)
                    index += 2
                    continue
            if ch == "`":
                run = len(text[index:]) - len(text[index:].lstrip("`"))
                close = self._closing_ticks(ticks, run, index + run)
                if close is not None:
                    flush()
                    code = text[index + run:close].replace("\n", " ")
                    if len(code) >= 2 and code[0] == code[-1] == " " and code.strip():
                        code = code[1:-1]
                    nodes.append(["html", f"<code>{_escape(code)}</code>"])
                    index = close + run
                else:
                    buffer.append("`" * run)
                    index += run
                continue
            if links and ch in "![":
                start = index + 1 if ch == "!" else index
                if start < length and text[start] == "[" and start in brackets:
                    found = self._link(text, start, brackets[start])
                    if found is not None:
                        flush()
                        label, url, title, end = found
                        nodes.append(["html", self._image(label, url, title) if ch == "!"
                                      else self._anchor(label, url, title)])
                        index = end
                        continue
            if ch == "<" and links:
                found = _AUTOLINK.match(text, index)
                href = self._href(found.group(1)) if found else None
                email = None if found else _EMAIL.match(text, index)
                if href is not None or email is not None:
                    flush()
                    shown = found.group(1) if found else email.group(1)
                    target = href if href is not None else f"mailto:{email.group(1)}"
                    nodes.append(["html", self._a(target, _escape(shown), "")])
                    index = (found or email).end()
                    continue
            if ch == "&":
                found = _ENTITY.match(text, index)
                if found:
                    buffer.append(html.unescape(found.group()))
                    index = found.end()
                    continue
            if ch in "*_~":
                run = len(text[index:]) - len(text[index:].lstrip(ch))
                before = text[index - 1] if index else " "
                after = text[index + run] if index + run < length else " "
                left = not _space(after) and (not _punctuation(after) or _space(before)
                                              or _punctuation(before))
                right = not _space(before) and (not _punctuation(before) or _space(after)
                                                or _punctuation(after))
                if ch == "_":
                    opens = left and (not right or _punctuation(before))
                    closes = right and (not left or _punctuation(after))
                elif ch == "~" and run > 2:
                    opens = closes = False
                else:
                    opens, closes = left, right
                flush()
                nodes.append(["delim", ch, run, opens, closes, run])
                index += run
                continue
            if ch == "\n":
                pending = "".join(buffer)
                kept = pending.rstrip(" ")
                buffer[:] = [kept]
                if len(pending) - len(kept) >= 2:
                    flush()
                    nodes.append(["html", "<br>\n"])
                else:
                    buffer.append("\n")
                index += 1
                while index < length and text[index] == " ":
                    index += 1
                continue
            if (links and ch in "hH" and (index == 0 or text[index - 1] in " \t\n(*_~")
                    and text.startswith(("http://", "https://"), index)):
                url = self._bare(_BARE.match(text, index).group())
                href = self._href(url)
                if href is not None and len(url) > len("https://"):
                    flush()
                    nodes.append(["html", self._a(href, _escape(url), "")])
                    index += len(url)
                    continue
            buffer.append(ch)
            index += 1
        flush()
        return self._emphasis(nodes)

    @staticmethod
    def _brackets(text: str) -> dict[int, int]:
        """The ] that closes each [, in one pass; escapes do not count."""
        found, stack, index = {}, [], 0
        while index < len(text):
            ch = text[index]
            if ch == "\\":
                index += 2
                continue
            if ch == "[":
                stack.append(index)
            elif ch == "]" and stack:
                found[stack.pop()] = index
            index += 1
        return found

    @staticmethod
    def _ticks(text: str) -> dict[int, list[int]]:
        """Where each run of backticks starts, by its length."""
        runs: dict[int, list[int]] = {}
        for found in re.finditer(r"`+", text):
            runs.setdefault(len(found.group()), []).append(found.start())
        return runs

    @staticmethod
    def _closing_ticks(ticks: dict[int, list[int]], run: int, after: int) -> int | None:
        starts = ticks.get(run, [])
        position = bisect.bisect_left(starts, after)
        return starts[position] if position < len(starts) else None

    @staticmethod
    def _bare(url: str) -> str:
        """A bare address without the punctuation that ends the sentence."""
        while url and url[-1] in ".,:;!?'\"*_~":
            url = url[:-1]
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        return url

    def _link(self, text: str, start: int, end: int):
        """The label, destination and title of a link or an image whose
        label is TEXT[START:END+1]; None when it is not one."""
        label = text[start + 1:end]
        index = end + 1
        if index < len(text) and text[index] == "(":
            found = self._destination(text, index)
            if found is not None:
                url, title, after = found
                return label, url, title, after
        if index < len(text) and text[index] == "[":
            close = text.find("]", index + 1)
            if close != -1 and close - index < 1000:
                name = text[index + 1:close] or label
                if _label(name) in self.refs:
                    url, title = self.refs[_label(name)]
                    return label, url, title, close + 1
        if _label(label) in self.refs:
            url, title = self.refs[_label(label)]
            return label, url, title, end + 1
        return None

    @staticmethod
    def _destination(text: str, index: int):
        length = len(text)
        position = index + 1
        while position < length and text[position] in " \t\n":
            position += 1
        if position < length and text[position] == "<":
            close = text.find(">", position)
            if close == -1 or "\n" in text[position:close]:
                return None
            url = text[position + 1:close]
            position = close + 1
        else:
            begin, depth = position, 0
            while position < length:
                ch = text[position]
                if ch == "\\" and position + 1 < length:
                    position += 2
                    continue
                if ch.isspace() or ord(ch) < 32:
                    break
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
                position += 1
            url = text[begin:position]
        while position < length and text[position] in " \t\n":
            position += 1
        title = ""
        if position < length and text[position] in "\"'(":
            closing = ")" if text[position] == "(" else text[position]
            close = position + 1
            while close < length and text[close] != closing:
                close += 2 if text[close] == "\\" else 1
            if close >= length:
                return None
            title = text[position + 1:close]
            position = close + 1
            while position < length and text[position] in " \t\n":
                position += 1
        if position < length and text[position] == ")":
            return _unescape(url), _unescape(title), position + 1
        return None

    def _resolve(self, path: str) -> PurePosixPath | None:
        """A path given in a document, as a path of the tree; None out of it."""
        path = unquote(path)
        joined = path.lstrip("/") if path.startswith("/") else posixpath.join(str(self.base), path)
        normal = posixpath.normpath(joined) if joined else "."
        if normal == "." or normal.startswith(("..", "/")):
            return None
        try:
            return check_file_path(normal)
        except BadRequest:
            return None

    def _href(self, url: str) -> str | None:
        """Where a link may go, or None: see the docstring of the module."""
        url = url.strip()
        if not url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
            return None
        scheme = _SCHEME.match(url)
        if scheme:
            name = scheme.group(1).lower()
            rest = url[scheme.end():]
            if name in ("http", "https") and rest.startswith("//") and len(rest) > 2:
                return url
            if name == "mailto" and rest:
                return url
            if name == "fleet" and _FLEET.fullmatch(rest):
                return f"fleet:{rest}"
            return None
        if url.startswith("#"):
            return url
        if url.startswith(("//", "\\")):
            return None
        path, _hash, fragment = url.partition("#")
        path = path.split("?", 1)[0]
        resolved = self._resolve(path) if path else None
        if resolved is None:
            return None
        return f"fleet:doc/{quote(str(resolved))}" + (f"#{fragment}" if fragment else "")

    @staticmethod
    def _a(href: str, inner: str, title: str) -> str:
        outside = href.startswith(("http:", "https:", "HTTP:", "HTTPS:", "mailto:"))
        extra = ' rel="noopener noreferrer" target="_blank"' if outside else ""
        titled = f' title="{_escape(title)}"' if title else ""
        return f'<a href="{_escape(href)}"{titled}{extra}>{inner}</a>'

    def _anchor(self, label: str, url: str, title: str) -> str:
        inner = self.inline(label, links=False)
        href = self._href(url)
        return inner if href is None else self._a(href, inner, title)

    def _image(self, label: str, url: str, title: str) -> str:
        alt = _TAG.sub("", self.inline(label, links=False))
        url = url.strip()
        path = None
        if url.startswith("fleet:doc/"):
            path = self._resolve("/" + url[len("fleet:doc/"):])
        elif url and not _SCHEME.match(url) and not url.startswith(("//", "\\", "#")):
            path = self._resolve(url.split("#", 1)[0].split("?", 1)[0])
        data = None
        mime = IMAGE_TYPES.get(path.suffix.lower()) if path is not None else None
        if mime and self.load is not None:
            data = self.load(path)
            if data is not None and (len(data) > MAX_IMAGE
                                     or self.spent + len(data) > MAX_IMAGES):
                data = None
        if data is None:
            self.refused.append(url[:300])
            titled = _escape(url[:300])
            return f'<span class="image-refused" title="{titled}">[{alt or titled}]</span>'
        self.spent += len(data)
        self.images += 1
        titled = f' title="{_escape(title)}"' if title else ""
        source = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        return f'<img src="{source}" alt="{alt}"{titled}>'

    def _emphasis(self, nodes: list[list]) -> str:
        """Pair the runs of *, _ and ~ into emphasis, strong and strikethrough;
        what is left of them is text."""
        index = 0
        while index < len(nodes):
            closer = nodes[index]
            if closer[0] != "delim" or not closer[4] or closer[2] == 0:
                index += 1
                continue
            opener_at = None
            for back in range(index - 1, max(-1, index - _EMPHASIS_REACH), -1):
                opener = nodes[back]
                if opener[0] != "delim" or opener[1] != closer[1] or not opener[3] \
                        or opener[2] == 0:
                    continue
                if closer[1] == "~":
                    if opener[2] != closer[2]:
                        continue
                elif (opener[3] and opener[4] or closer[3] and closer[4]) \
                        and (opener[5] + closer[5]) % 3 == 0 \
                        and not (opener[5] % 3 == 0 and closer[5] % 3 == 0):
                    continue
                opener_at = back
                break
            if opener_at is None:
                index += 1
                continue
            opener = nodes[opener_at]
            if closer[1] == "~":
                used, tag = closer[2], "del"
            else:
                used = 2 if opener[2] >= 2 and closer[2] >= 2 else 1
                tag = "strong" if used == 2 else "em"
            inner = self._text(nodes[opener_at + 1:index])
            opener[2] -= used
            closer[2] -= used
            nodes[opener_at + 1:index] = [["html", f"<{tag}>{inner}</{tag}>"]]
            index = opener_at + 2
            if opener[2] == 0:
                del nodes[opener_at]
                index -= 1
            if closer[2] == 0:
                del nodes[index]
        return self._text(nodes)

    @staticmethod
    def _text(nodes: list[list]) -> str:
        parts = []
        for node in nodes:
            if node[0] == "text":
                parts.append(_escape(node[1]))
            elif node[0] == "html":
                parts.append(node[1])
            else:
                parts.append(_escape(node[1] * node[2]))
        return "".join(parts)


def render(text: str, base: PurePosixPath | str = "",
           load: Callable[[PurePosixPath], bytes | None] | None = None) -> Rendered:
    """TEXT as HTML. BASE is the folder of the document in the tree, which
    relative links and images are taken from; LOAD gives the bytes of a
    file of the tree, or None, and without it no image is shown."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "\ufffd").split("\n")
    lines = [line.expandtabs(4) for line in lines]
    meta, lines = front_matter(lines)
    renderer = _Renderer(PurePosixPath(base), load)
    lines = renderer.definitions(lines)
    body = renderer.blocks(lines)
    title = meta.get("title") or next(
        (heading["text"] for heading in renderer.headings if heading["level"] == 1), "")
    return Rendered(body, meta, title, renderer.headings, renderer.images, renderer.refused)
