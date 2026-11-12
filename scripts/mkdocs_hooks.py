"""MkDocs hooks for the documentation site (mkdocs.yml: `hooks`).

The Markdown in docs/ is written for GitHub, where it is read too. Two differences matter on
the site; these hooks handle them at build time, so the sources stay as they are.

1. Links that leave docs/. The docs link to evaluation reports, datasets, seed data and source
   files elsewhere in the repository.
   - Markdown files under MOUNTS (the evaluation README and its reports) are added to the site
     at the same path, and links to them point there.
   - Any other link to a file outside docs/ points to the repository when DOCS_REPO_URL is set
     (the docs workflow sets it), and becomes plain text otherwise (a local build), so
     `mkdocs build --strict` passes either way. A link to a file that does not exist is left
     alone, so the strict build still reports it.

2. GitHub-flavoured lists. GitHub starts a list right after a paragraph line and nests lists
   indented by two or three spaces; Python-Markdown needs a blank line before a list and four
   spaces per level. Each page's Markdown is adjusted before it is rendered; fenced code is
   left alone, except that a fence inside a list item moves with its item.
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element

from markdown import Extension, Markdown
from markdown.treeprocessors import Treeprocessor
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page

# Repository directories whose Markdown files are published with the docs, at the same path.
MOUNTS = ("evals",)

_state: dict[str, Any] = {"page": None, "mounted": {}, "site_files": set()}


def _root(config: MkDocsConfig) -> Path:
    return Path(config.config_file_path).resolve().parent


def _docs_prefix(config: MkDocsConfig) -> str:
    return Path(config.docs_dir).resolve().relative_to(_root(config)).as_posix()


# --- Mounted files ------------------------------------------------------------------------------


def on_files(files: Files, config: MkDocsConfig) -> Files:
    root = _root(config)
    mounted: dict[str, str] = {}
    for mount in MOUNTS:
        for path in sorted((root / mount).rglob("*.md")):
            uri = path.relative_to(root).as_posix()
            if files.get_file_from_path(uri) is not None:
                raise ValueError(f"{uri} exists in docs/ and in the repository: rename one")
            files.append(File.generated(config, uri, abs_src_path=str(path)))
            mounted[uri] = uri
    _state["mounted"] = mounted
    _state["site_files"] = {f.src_uri for f in files}
    return files


def on_serve(server: Any, config: MkDocsConfig, builder: Any) -> Any:
    for mount in MOUNTS:
        server.watch(str(_root(config) / mount))
    return server


# --- Links that leave docs/ ---------------------------------------------------------------------


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    config.markdown_extensions.append(_RepositoryLinks(config))  # pyright: ignore[reportArgumentType]
    return config


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    _state["page"] = page
    return gfm_lists(markdown)


class _RepositoryLinks(Extension):
    def __init__(self, config: MkDocsConfig) -> None:
        super().__init__()
        self.config = config

    def extendMarkdown(self, md: Markdown) -> None:  # noqa: N802 (Python-Markdown's name)
        # After inline parsing (20), before MkDocs resolves and validates links ("relpath", 0).
        md.treeprocessors.register(_LinkRewriter(md, self.config), "workmind_repo_links", 2)


class _LinkRewriter(Treeprocessor):
    def __init__(self, md: Markdown, config: MkDocsConfig) -> None:
        super().__init__(md)
        self.config = config

    def run(self, root: Element) -> None:
        page: Page | None = _state["page"]
        if page is None:
            return
        for element in root.iter():
            attribute = {"a": "href", "img": "src"}.get(element.tag)
            if attribute is None or (url := element.get(attribute)) is None:
                continue
            new = self._rewrite(page, url, image=element.tag == "img")
            if new is None:  # outside docs/ and no repository URL: plain text
                element.tag = "span"
                del element.attrib[attribute]
                element.set("class", "repo-path")
                element.set("title", f"{_repo_target(self.config, page, url)} in the repository")
            elif new != url:
                element.set(attribute, new)

    def _rewrite(self, page: Page, url: str, *, image: bool) -> str | None:
        scheme, netloc, path, _query, fragment = urlsplit(url)
        if scheme or netloc or not path or url.startswith(("/", "\\")):
            return url
        target = _repo_target(self.config, page, url)
        if target.startswith("../"):  # outside the repository: MkDocs reports it
            return url
        docs = _docs_prefix(self.config)
        site_uri = _state["mounted"].get(target)
        if site_uri is None and target.startswith(f"{docs}/"):
            site_uri = target.removeprefix(f"{docs}/")
            if site_uri not in _state["site_files"]:
                return url  # a docs/ path that is not a file: MkDocs reports it
        if site_uri is not None:
            page_dir = posixpath.dirname(page.file.src_uri)
            relative = posixpath.relpath(site_uri, page_dir or ".")
            return relative + (f"#{fragment}" if fragment else "")
        on_disk = _root(self.config) / target
        if not on_disk.exists():
            return url  # a broken link: MkDocs reports it
        repo_url = (self.config.repo_url or "").rstrip("/")
        if not repo_url:
            return None
        ref = os.environ.get("DOCS_REPO_REF", "main")
        kind = "raw" if image else "tree" if on_disk.is_dir() else "blob"
        return f"{repo_url}/{kind}/{ref}/{target}" + (f"#{fragment}" if fragment else "")


def _repo_target(config: MkDocsConfig, page: Page, url: str) -> str:
    """The repository path a relative link on `page` points to."""
    src = page.file.src_uri
    page_path = src if src in _state["mounted"] else f"{_docs_prefix(config)}/{src}"
    path = unquote(urlsplit(url).path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(page_path), path))


# --- GitHub-flavoured lists ---------------------------------------------------------------------

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")
_ITEM = re.compile(r"^( *)([-*+]|\d{1,9}[.)])( +)(?=\S)")
_BLOCK_START = re.compile(r"^(#|\||>|```|~~~|<)")


def gfm_lists(markdown: str) -> str:
    """Rewrite GitHub-style lists for Python-Markdown (see the module docstring)."""
    out: list[str] = []
    # Open list items: (source content column, rendered marker indent).
    stack: list[tuple[int, int]] = []
    fence: str | None = None  # the closing marker of the open fence
    fence_shift = 0
    previous = ""
    for line in markdown.split("\n"):
        if fence is not None:
            out.append(_shift(line, fence_shift))
            if line.strip().startswith(fence) and line.strip().strip(fence[0]) == "":
                fence = None
            previous = line
            continue
        indent = len(line) - len(line.lstrip(" "))
        blank = not line.strip()
        item = _ITEM.match(line)
        rendered = indent
        if item and _starts_list(item, previous, in_list=bool(stack)):
            in_list = bool(stack)
            while stack and indent < stack[-1][0]:
                stack.pop()
            rendered = stack[-1][1] + 4 if stack else indent
            if not in_list and previous.strip():
                out.append("")  # GitHub lets a list interrupt a paragraph
            content = indent + len(item.group(2)) + min(len(item.group(3)), 4)
            stack.append((content, rendered))
        elif stack and not blank and indent == 0:
            if not previous.strip() or _BLOCK_START.match(line):
                stack.clear()  # the list ended; otherwise a lazy continuation line
        elif stack and not blank:
            owner = next((s for s in reversed(stack) if s[0] <= indent), stack[0])
            rendered = owner[1] + 4 + max(0, indent - owner[0])
        out.append(" " * rendered + line.lstrip(" ") if rendered != indent else line)
        if (opened := _FENCE.match(line)) is not None:
            fence, fence_shift = opened.group(2), rendered - indent
        previous = line
    return "\n".join(out)


def _starts_list(item: re.Match[str], previous: str, *, in_list: bool) -> bool:
    """GitHub: inside a list every marker is an item; a paragraph is interrupted by a bullet,
    or by an ordered item only when it is numbered 1."""
    if in_list or not previous.strip() or _ITEM.match(previous):
        return True
    marker = item.group(2)
    return marker in "-*+" or marker[:-1] == "1"


def _shift(line: str, shift: int) -> str:
    if shift > 0:
        return " " * shift + line
    if shift < 0:
        removable = len(line) - len(line.lstrip(" "))
        return line[min(-shift, removable) :]
    return line
