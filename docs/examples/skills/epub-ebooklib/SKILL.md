---
name: epub-ebooklib
description: Build an EPUB with Python ebooklib without producing empty chapters; use whenever creating .epub files.
---

# Building EPUBs with ebooklib

- Run with `/usr/bin/python3` if the default python lacks ebooklib.
- Set chapter content as BYTES with no XML declaration: `ch.content = html.encode("utf-8")`, where `html` starts with `<html ...>` and NOT `<?xml ...?>`. A str starting with `<?xml` silently serializes as an empty chapter.
- Escape text (`html.escape`) and wrap paragraphs in `<p>`.
- Add every chapter to `book.spine` and a `epub.EpubNcx()` / `epub.EpubNav()`.
- Right after `epub.write_epub`, verify once: unzip the file, check each chapter xhtml is larger than ~500 bytes. Fix at once if not, then stop; do not re-verify repeatedly.
