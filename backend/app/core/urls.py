"""Pure-stdlib URL/filename helpers, extracted from ingestion/download.py so
app/core depends on nothing in ingestion/, while ingestion/download.py imports
them back from here. The API's GET /pdf route needs filename_from_url() without
pulling in the ingestion package (docling, torch, etc.)."""
from pathlib import Path
from urllib.parse import unquote, urlparse


def filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = unquote(Path(path).name)
    # keep filesystem-safe, strip any remaining unsafe chars
    name = "".join(c for c in name if c not in '<>:"|?*')
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def load_urls(urls_file: Path) -> list[str]:
    urls = []
    for line in urls_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls
