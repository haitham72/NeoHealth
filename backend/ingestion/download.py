"""Download the ReguLense seed corpus. Reads corpus_urls.txt, saves PDFs to dataset/."""
import requests

from app.core.config import CORPUS_URLS_FILE, DATASET_DIR
from app.core.urls import filename_from_url, load_urls


def safe(s: str) -> str:
    """Windows' cp1252 console can't print non-Latin-1 filenames (e.g. the Arabic-titled
    DHA PDF) -- an uncaught UnicodeEncodeError here would abort the run after the file
    was already written to disk, so print output is made ASCII-safe unconditionally."""
    return s.encode("ascii", "backslashreplace").decode("ascii")


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def main():
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    urls = load_urls(CORPUS_URLS_FILE)
    print(f"Found {len(urls)} URLs in {CORPUS_URLS_FILE.name}")

    ok, skipped, failed = [], [], []
    for url in urls:
        fname = filename_from_url(url)
        dest = DATASET_DIR / fname
        if dest.exists() and dest.stat().st_size > 0:
            skipped.append(fname)
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            ok.append(fname)
            print(f"  OK   {safe(fname)} ({len(resp.content)/1024:.0f} KB)")
        except Exception as e:
            failed.append((fname, str(e)))
            print(f"  FAIL {safe(fname)} - {safe(str(e))}")

    print("\n--- Summary ---")
    print(f"Downloaded: {len(ok)}")
    print(f"Skipped (already present): {len(skipped)}")
    print(f"Failed: {len(failed)}")
    for fname, err in failed:
        print(f"  {safe(fname)}: {safe(err)}")

    total_on_disk = len(list(DATASET_DIR.glob("*.pdf")))
    print(f"Total PDFs on disk: {total_on_disk}")


if __name__ == "__main__":
    main()
