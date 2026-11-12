"""Model input and output hygiene (docs/security/owasp-llm-top10.md, LLM01 and LLM05).

Text that a model reads may come from anyone who can write a document, a Slack message, a
meeting transcript or a web page. Text that a model writes may have been steered by it. So:

- `quote_markers`: citation markers inside data ("[S2]", "[T1]") lose their brackets before a
  model reads them. A document cannot pre-write a marker that the model copies and the app then
  shows as a citation of a different, trusted source (citation spoofing).
- `without_images`: a model's answer never carries a markdown or HTML image. Clients that load
  images (MCP clients, chat apps, a future API consumer) would otherwise send the image URL,
  and whatever data the model put in it, to its host as soon as the answer is shown. The web
  app also never loads them (components/markdown/markdown-image.tsx); this covers every other
  client, because the stored answer and the MCP `ask` result go through it.
- `escape_tags`: angle brackets in data are escaped, so data cannot close the element that
  delimits it (`</transcript>`, `</external>`) and pose as instructions.
"""

import re

# [S1], [S1, S3], [ T2 ], as models and documents write them; Q for analytics queries.
_MARKERS = re.compile(r"\[(\s*[STQ]\d+(?:\s*,\s*[STQ]\d+)*\s*)\]")
# ![alt](url "title"), ![alt][ref], ![alt] (a shortcut reference) and <img ...> tags.
_MD_IMAGE = re.compile(r"!\[([^\]\n]*)\](?:\s?\([^)\n]*\)?|\[[^\]\n]*\])?")
_HTML_IMAGE = re.compile(r"<img\b[^>]*>?", re.IGNORECASE)


def quote_markers(text: str) -> str:
    """'[S2]' in data becomes '(S2)': still readable, no longer a citation marker."""
    return _MARKERS.sub(r"(\1)", text)


def without_images(text: str) -> str:
    """Every image becomes '[image removed: alt]'; the URL is dropped."""

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        return f"[image removed: {alt}]" if alt else "[image removed]"

    text = _MD_IMAGE.sub(replace, text)
    return _HTML_IMAGE.sub("[image removed]", text)


def escape_tags(text: str) -> str:
    """'<' and '>' escaped, '&' kept: data cannot open or close a delimiting element."""
    return text.replace("<", "&lt;").replace(">", "&gt;")
