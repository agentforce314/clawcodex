# Example skills for local models

Two small skills that cut wasted turns when a slow local model (e.g.
qwen3 on Ollama, ~10 tok/s) runs unguided. Copy a folder into
`~/.clawcodex/skills/` (all projects) or `<project>/.clawcodex/skills/`.

| Skill | Prevents |
|---|---|
| `scrape-cloudflare-site` | Burning turns on direct curl / headless Chrome against a Cloudflare challenge; endless retries on a "blocked until" 403 |
| `epub-ebooklib` | The ebooklib empty-chapter bug (`str` content starting with `<?xml`) and repeated re-verification |

Under `--nano` the model sees each skill's name, description and
location and loads the file with Read (see `docs/nano.md`).
