import re

from bs4 import BeautifulSoup

_QUOTE_START = re.compile(r"^(On .+ wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+)$", re.I)
_SIG_START = re.compile(r"^(--\s*|Sent from my .+|Get Outlook for .+)$", re.I)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for q in soup.select("blockquote, .gmail_quote"):
        q.decompose()
    return soup.get_text("\n")


def clean_body(text: str, is_html: bool = False) -> str:
    """Strip HTML, quoted replies and signatures; keep the new text only."""
    if is_html:
        text = html_to_text(text)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if _QUOTE_START.match(s) or _SIG_START.match(s):
            break
        if s.startswith(">"):
            continue
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
