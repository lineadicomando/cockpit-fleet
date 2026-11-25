"""The renderer of the documents: a corpus of Markdown, and what it never lets through."""

import base64
import json
import random
import time
import unittest
from html.parser import HTMLParser

from test_documents import DocumentsTest

from cockpit_fleet import docrender, paths

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

CORPUS = [
    ("# One\n## Two ##\n###### Six", '<h1 id="one">One</h1>\n<h2 id="two">Two</h2>\n<h6 id="six">Six</h6>'),
    ("Title\n=====\nSub\n---", '<h1 id="title">Title</h1>\n<h2 id="sub">Sub</h2>'),
    ("#nospace", "<p>#nospace</p>"),
    ("# Same\n# Same", '<h1 id="same">Same</h1>\n<h1 id="same-1">Same</h1>'),
    ("a\nb\n\nc", "<p>a\nb</p>\n<p>c</p>"),
    ("a  \nb\\\nc", "<p>a<br>\nb<br>\nc</p>"),
    ("*a* _b_ **c** __d__ ~~e~~ ~f~", "<p><em>a</em> <em>b</em> <strong>c</strong> <strong>d</strong> <del>e</del> <del>f</del></p>"),
    ("snake_case_name and 2*3*4", "<p>snake_case_name and 2<em>3</em>4</p>"),
    ("**a *b* c**", "<p><strong>a <em>b</em> c</strong></p>"),
    ("*not closed", "<p>*not closed</p>"),
    ("\\*literal\\*", "<p>*literal*</p>"),
    ("`a < b` and ``x ` y``", "<p><code>a &lt; b</code> and <code>x ` y</code></p>"),
    ("```\n<raw>\n```", "<pre><code>&lt;raw&gt;\n</code></pre>"),
    ("~~~sh\nls\n~~~", '<pre><code class="language-sh">ls\n</code></pre>'),
    ("```\nnot closed", "<pre><code>not closed\n</code></pre>"),
    ("    indented\n    code", "<pre><code>indented\ncode\n</code></pre>"),
    ("> q\n> > nested", "<blockquote>\n<p>q</p>\n<blockquote>\n<p>nested</p>\n</blockquote>\n</blockquote>"),
    ("- a\n- b", "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"),
    ("- a\n\n- b", "<ul>\n<li><p>a</p></li>\n<li><p>b</p></li>\n</ul>"),
    ("3. c\n4. d", '<ol start="3">\n<li>c</li>\n<li>d</li>\n</ol>'),
    ("- a\n  - b\n    - c", "<ul>\n<li>a\n<ul>\n<li>b\n<ul>\n<li>c</li>\n</ul></li>\n</ul></li>\n</ul>"),
    ("- [ ] todo\n- [x] done", '<ul>\n<li class="task"><input type="checkbox" disabled> todo</li>\n<li class="task"><input type="checkbox" disabled checked> done</li>\n</ul>'),
    ("- a\n+ b", "<ul>\n<li>a</li>\n</ul>\n<ul>\n<li>b</li>\n</ul>"),
    ("text\n- item", "<p>text</p>\n<ul>\n<li>item</li>\n</ul>"),
    ("text\n2. not a list", "<p>text\n2. not a list</p>"),
    ("***\n---\n___", "<hr>\n<hr>\n<hr>"),
    ("| a | b |\n|---|:-:|\n| 1 | 2 |\n| 3 |", '<table>\n<thead>\n<tr><th>a</th><th class="align-center">b</th></tr>\n</thead>\n<tbody>\n<tr><td>1</td><td class="align-center">2</td></tr>\n<tr><td>3</td><td class="align-center"></td></tr>\n</tbody>\n</table>'),
    ("| a |\n| - | - |", "<p>| a |\n| - | - |</p>"),
    ("[x](https://e.org \"T\")", '<p><a href="https://e.org" title="T" rel="noopener noreferrer" target="_blank">x</a></p>'),
    ("[x](<a b.md>)", '<p><a href="fleet:doc/dir/a%20b.md">x</a></p>'),
    ("[x](../up.md) [y](/root.md) [z](#here)", '<p><a href="fleet:doc/up.md">x</a> <a href="fleet:doc/root.md">y</a> <a href="#here">z</a></p>'),
    ("[out](../../etc/passwd)", "<p>out</p>"),
    ("[r](fleet:run/42) [h](fleet:host/web01)", '<p><a href="fleet:run/42">r</a> <a href="fleet:host/web01">h</a></p>'),
    ("[m](mailto:a@b.c) <a@b.c>", '<p><a href="mailto:a@b.c" rel="noopener noreferrer" target="_blank">m</a> <a href="mailto:a@b.c" rel="noopener noreferrer" target="_blank">a@b.c</a></p>'),
    ("see https://e.org/a_(b). end", '<p>see <a href="https://e.org/a_(b)" rel="noopener noreferrer" target="_blank">https://e.org/a_(b)</a>. end</p>'),
    ("[a][r] [r][] [r]\n\n[r]: https://e.org", '<p><a href="https://e.org" rel="noopener noreferrer" target="_blank">a</a> <a href="https://e.org" rel="noopener noreferrer" target="_blank">r</a> <a href="https://e.org" rel="noopener noreferrer" target="_blank">r</a></p>'),
    ("[no ref]", "<p>[no ref]</p>"),
    ("&copy; &amp;lt; &#65; &bogus;", "<p>© &amp;lt; A &amp;bogus;</p>"),
    ("<div>x</div>", "<p>&lt;div&gt;x&lt;/div&gt;</p>"),
]

ATTACKS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[a](javascript:alert(1))",
    "[a](JaVaScRiPt:alert(1))",
    "[a](java\tscript:alert(1))",
    "[a](&#106;avascript:alert(1))",
    "[a](\\javascript:alert(1))",
    "[a](data:text/html;base64,PHNjcmlwdD4=)",
    "[a](vbscript:x) [b](file:///etc/passwd) [c](//evil.org/x)",
    "<javascript:alert(1)>",
    "![x](https://evil.org/track.png)",
    "![x](http://evil.org/track.png \"t\")",
    "![x](//evil.org/a.png)",
    "![x](data:image/png;base64,AAAA)",
    "![x](../../outside.png)",
    "![x](fleet:doc/../x.png)",
    "[x](https://e.org\" onmouseover=\"alert(1))",
    "[x](https://e.org \"a\" onclick=\"b\")",
    "[\"><script>](https://e.org)",
    "![\"onerror=alert(1) x](pic.png)",
    "```\"><script>alert(1)</script>\n```",
    "```js\"onload=x\nx\n```",
    "# <svg onload=alert(1)>",
    "| <b> | x |\n|---|---|\n| <i onclick=x> | y |",
    "[r]\n\n[r]: javascript:alert(1)",
    "<https://e.org/\"onclick=alert(1)>",
    "https://e.org/\"><script>",
    "[a](fleet:run/1\"onclick=x)",
    "- [x] <script>\n  > `<script>`",
    "\0<script>\0",
]

ALLOWED = {
    "h1": set(), "h2": set(), "h3": set(), "h4": set(), "h5": set(), "h6": set(),
    "p": set(), "br": set(), "em": set(), "strong": set(), "del": set(), "code": {"class"},
    "pre": set(), "blockquote": set(), "ul": set(), "ol": {"start"}, "li": {"class"},
    "input": {"type", "disabled", "checked"}, "hr": set(), "table": set(), "thead": set(),
    "tbody": set(), "tr": set(), "th": {"class"}, "td": {"class"},
    "a": {"href", "title", "rel", "target"}, "img": {"src", "alt", "title"},
    "span": {"class", "title"},
}
ALLOWED_HREF = ("https://", "http://", "mailto:", "fleet:", "#")


class Checker(HTMLParser):
    def __init__(self, test):
        super().__init__(convert_charrefs=True)
        self.test = test

    def handle_starttag(self, tag, attrs):
        self.test.assertIn(tag, ALLOWED, tag)
        for name, value in attrs:
            self.test.assertIn(name, ALLOWED[tag], (tag, name))
            if name == "id":
                continue
            if name == "href":
                self.test.assertTrue(value.startswith(ALLOWED_HREF), value)
            if name == "src":
                self.test.assertTrue(value.startswith("data:image/"), value)
            if name == "class" and tag in ("th", "td"):
                self.test.assertRegex(value, r"^align-(left|right|center)$")
            if name == "type":
                self.test.assertEqual(value, "checkbox")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def check(test, output):
    # Headings carry an id, the only attribute not in the table.
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        ALLOWED[tag] = {"id"}
    Checker(test).feed(output)


class RenderTest(unittest.TestCase):
    def render(self, text, files=None):
        files = files or {}
        return docrender.render(text, "dir", lambda path: files.get(str(path)))

    def test_the_corpus(self):
        for source, expected in CORPUS:
            self.assertEqual(self.render(source).html, expected, source)

    def test_nothing_but_its_own_tags_attributes_and_links(self):
        for attack in ATTACKS:
            output = self.render(attack, {"dir/pic.png": PNG}).html
            check(self, output)
            lowered = output.lower()
            # As text, escaped, any of them may stay: never as a tag or an attribute.
            self.assertNotIn("<script", lowered, attack)
            for word in ("onerror=", "onload=", "onclick=", "onmouseover="):
                self.assertNotRegex(lowered, rf"<[^>]*\s{word}", (attack, output))
            for word in ("javascript:", "data:text", "evil.org", "file:", "vbscript:"):
                self.assertNotRegex(lowered, rf'(href|src)="[^"]*{word}', (attack, output))

    def test_random_input_breaks_nothing(self):
        pieces = ["*", "**", "_", "~~", "`", "``", "[", "]", "(", ")", "!", "<", ">", "#", "-",
                  "|", "\\", "\n", "\n\n", " ", "    ", "1. ", "> ", "- [ ] ", "```", "&",
                  "a", "b", "https://x.org", "javascript:", "\"", "'", "<script>", "é"]
        generator = random.Random(20)  # noqa: S311  (the same input every time)
        for _ in range(3000):
            text = "".join(generator.choice(pieces) for _ in range(generator.randint(1, 40)))
            check(self, self.render(text).html)

    def test_images_come_from_the_tree_within_the_budget(self):
        shown = self.render("![dot](pic.png) ![far](sub/../pic.png) ![root](/dir/pic.png)",
                            {"dir/pic.png": PNG})
        self.assertEqual(shown.images, 3)
        self.assertIn(f'src="data:image/png;base64,{base64.b64encode(PNG).decode()}"',
                      shown.html)
        refused = self.render("![a](missing.png) ![b](notes.txt) ![c](https://e.org/a.png)",
                              {"dir/notes.txt": b"x"})
        self.assertEqual((refused.images, refused.refused),
                         (0, ["missing.png", "notes.txt", "https://e.org/a.png"]))
        self.assertIn('<span class="image-refused" title="https://e.org/a.png">[c]</span>',
                      refused.html)
        big = b"x" * (docrender.MAX_IMAGES // 4 - 1)
        many = self.render("\n".join(f"![{n}](p{n}.png)" for n in range(6)),
                           {f"dir/p{n}.png": big for n in range(6)})
        self.assertEqual(many.images, 4)

    def test_front_matter_title_and_headings(self):
        rendered = self.render("---\ntitle: \"Report\"\nauthor: anna\n---\n# Heading\n## Sub")
        self.assertEqual(rendered.meta, {"title": "Report", "author": "anna"})
        self.assertEqual(rendered.title, "Report")
        self.assertEqual([(h["level"], h["id"]) for h in rendered.headings],
                         [(1, "heading"), (2, "sub")])
        self.assertNotIn("author", rendered.html)
        self.assertEqual(self.render("---\nnot closed").html, "<hr>\n<p>not closed</p>")
        self.assertEqual(self.render("# Only\ntext").title, "Only")

    def test_deep_nesting_ends_as_text(self):
        output = self.render("> " * 200 + "deep").html
        self.assertLessEqual(output.count("<blockquote>"), 20)
        self.assertIn("deep", output)

    def test_a_long_text_is_rendered_in_time(self):
        text = ("[" * 2000 + "`" * 500 + "*a" * 5000 + "\n") * 20
        started = time.monotonic()
        self.render(text)
        self.assertLess(time.monotonic() - started, 10)


class CommandTest(DocumentsTest):
    def test_a_document_rendered_with_its_images(self):
        self.written("reports/r.md", "# R\n\n![g](graph.png) [other](../notes.md)\n")
        self.put("reports/graph.png", PNG)
        rendered = self.ok("doc.render", {"path": "reports/r.md"}, role="viewer")
        self.assertEqual((rendered["title"], rendered["images"]), ("R", 1))
        self.assertIn('href="fleet:doc/notes.md"', rendered["html"])
        without = self.ok("doc.render", {"path": "reports/r.md", "images": False})
        self.assertEqual(without["images"], 0)
        preview = self.ok("doc.render", {"path": "reports/new.md", "content": "![g](graph.png)"})
        self.assertEqual(preview["images"], 1)
        self.assertEqual(self.code("doc.render", {"path": "reports/graph.png"}), "bad_request")
        self.assertEqual(self.code("doc.render", {"path": "missing.md"}), "not_found")
        json.dumps(rendered)

    def test_an_image_is_never_read_through_a_link(self):
        outside = self.root / "secret.png"
        outside.write_bytes(PNG)
        self.written("a.md", "![s](s.png)")
        (paths.DOCUMENTS_DIR / "s.png").symlink_to(outside)
        self.assertEqual(self.ok("doc.render", {"path": "a.md"})["images"], 0)
