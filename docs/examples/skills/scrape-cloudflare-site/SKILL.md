---
name: scrape-cloudflare-site
description: Fetch web pages (e.g. novel chapters) that return a Cloudflare "Just a moment" page to curl or headless Chrome.
---

# Scraping pages behind Cloudflare

Do NOT try direct curl or headless Chrome first; both get the challenge page and waste turns.

1. Fetch through the reader proxy: `curl -s "https://r.jina.ai/<full original URL>"`. The real text follows a `Markdown Content:` marker.
2. Write ONE Python script that loops over all pages and saves each to disk, rather than one tool call per page. Skip files already saved so reruns resume.
3. Sleep ~1s between requests. A ~240-byte body or `RateLimitTriggeredError` means HTTP 429: wait 10-30s and retry.
4. Multi-page chapters may continue at `<name>_2.html`; fetch and join them.
5. After the loop, print count and byte size per file; re-fetch any that are tiny.
6. If the proxy returns a 403 `AbuseAlleviationError` saying "blocked until <time>", STOP. Do not poll or loop; every retry extends the block. Tell the user the time and finish.
