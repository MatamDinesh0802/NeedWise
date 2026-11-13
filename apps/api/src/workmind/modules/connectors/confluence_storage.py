"""Confluence storage format to Markdown text.

The storage format is XHTML plus Confluence's own elements (`ac:` macros and links, `ri:`
resource identifiers; https://confluence.atlassian.com/doc/confluence-storage-format-790796544.html).
What a reader sees becomes text: headings (one level below the page title), paragraphs, lists,
tables as rows, code blocks, task lists, link texts, dates. Configuration and decoration do not:
macro parameters, images, emoticons, placeholders. Macros that wrap content (info, note, panel,
expand, ...) keep their content.
"""

import html
import re
from html.parser import HTMLParser

_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCKS = frozenset(
    {"p", "div", "blockquote", "section", "table", "ac:layout-section", "ac:layout-cell"}
)
# Not page text: macro configuration, media, decoration, instructions to the author.
_DROPPED = frozenset(
    {
        "ac:parameter",
        "ac:image",
        "ac:emoticon",
        "ac:placeholder",
        "ac:task-id",
        "ac:task-uuid",
        "script",
        "style",
    }
)


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._dropped = 0
        self._pre = 0
        self._lists: list[list[int]] = []  # per open list: [ordered (0/1), counter]
        self._links: list[dict[str, str | bool]] = []
        self._task_status: list[str] = []
        self._capture_status = False
        self._row_open = False

    # --- output helpers -----------------------------------------------------------------------

    def _tail(self) -> str:
        return "".join(self.out[-3:])

    def _block(self) -> None:
        """Starts a new paragraph unless one was just started."""
        if self.out and not self._tail().endswith("\n\n"):
            self.out.append("\n" if self._tail().endswith("\n") else "\n\n")

    def _line(self) -> None:
        if self.out and not self._tail().endswith("\n"):
            self.out.append("\n")

    def _emit(self, text: str) -> None:
        if not text:
            return
        if self._links:
            self._links[-1]["texted"] = True
        self.out.append(text)

    # --- parser callbacks ---------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag in _DROPPED:
            self._dropped += 1
            return
        if self._dropped:
            return
        if tag in _HEADINGS:
            self._block()
            self._emit("#" * min(_HEADINGS[tag] + 1, 6) + " ")  # the page title is the H1
        elif tag in _BLOCKS:
            self._block()
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("ul", "ol", "ac:task-list"):
            if not self._lists:
                self._block()
            self._lists.append([1 if tag == "ol" else 0, 0])
        elif tag == "li":
            self._list_item()
        elif tag == "ac:task":
            self._task_status.append("")
        elif tag == "ac:task-status":
            self._capture_status = True
        elif tag == "ac:task-body":
            done = bool(self._task_status) and self._task_status[-1] == "complete"
            self._line()
            self._emit("  " * max(len(self._lists) - 1, 0) + ("- [x] " if done else "- [ ] "))
        elif tag == "tr":
            self._line()
            self._emit("| ")
            self._row_open = True
        elif tag in ("pre", "ac:plain-text-body"):
            self._block()
            self.out.append("```\n")
            self._pre += 1
        elif tag == "ac:link":
            self._links.append({"fallback": "", "texted": False})
        elif tag in ("ri:page", "ri:blog-post", "ri:attachment", "ri:space") and self._links:
            title = (
                values.get("ri:content-title")
                or values.get("ri:filename")
                or values.get("ri:space-key")
            )
            if title and not self._links[-1]["fallback"]:
                self._links[-1]["fallback"] = title
        elif tag == "time" and values.get("datetime"):
            self._emit(values["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self._dropped = max(self._dropped - 1, 0)
            return
        if self._dropped:
            return
        if tag in _HEADINGS or tag in _BLOCKS:
            self._block()
        elif tag in ("ul", "ol", "ac:task-list"):
            if self._lists:
                self._lists.pop()
            if not self._lists:
                self._block()
        elif tag in ("li", "ac:task-body"):
            self._line()
        elif tag == "ac:task":
            if self._task_status:
                self._task_status.pop()
        elif tag == "ac:task-status":
            self._capture_status = False
        elif tag in ("th", "td"):
            self._emit(" | ")
        elif tag == "tr":
            self._row_open = False
            self._line()
        elif tag in ("pre", "ac:plain-text-body"):
            self._pre = max(self._pre - 1, 0)
            self._line()
            self.out.append("```\n\n")
        elif tag == "ac:link" and self._links:
            link = self._links.pop()
            if not link["texted"] and link["fallback"]:
                self._emit(str(link["fallback"]))

    def handle_data(self, data: str) -> None:
        if self._dropped:
            return
        if self._capture_status:
            if self._task_status:
                self._task_status[-1] += data.strip()
            return
        if self._pre:
            self._emit(data)
            return
        text = _SPACES.sub(" ", data)
        if not text.strip():
            if self.out and not self._tail().endswith((" ", "\n")) and text:
                self.out.append(" ")
            return
        if self._tail().endswith("\n") or not self.out:
            text = text.lstrip()
        self._emit(text)

    def _list_item(self) -> None:
        self._line()
        depth = max(len(self._lists) - 1, 0)
        if self._lists and self._lists[-1][0]:
            self._lists[-1][1] += 1
            marker = f"{self._lists[-1][1]}. "
        else:
            marker = "- "
        self._emit("  " * depth + marker)


def storage_to_markdown(storage: str) -> str:
    """The text of a page body in storage format, as Markdown."""
    # CDATA (code macro bodies, plain-text link bodies) is literal text: escape it so the HTML
    # parser reads it back as data.
    source = _CDATA.sub(lambda m: html.escape(m.group(1), quote=False), storage)
    converter = _Converter()
    converter.feed(source)
    converter.close()
    text = "".join(converter.out)
    lines = [line.rstrip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip() + "\n"
