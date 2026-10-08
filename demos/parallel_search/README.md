# Parallel Search MCP example

Run free, keyless web search and page extraction through ClawCodex's existing
Streamable HTTP MCP client. The [Parallel Search MCP documentation](https://docs.parallel.ai/integrations/mcp/search-mcp)
describes the anonymous tier: it uses `fast` search mode, has rate limits, and
caps excerpts to roughly 25,000 characters per call. Model inference is separate.

## Run the example

From a fresh checkout of ClawCodex, install its declared dependencies:

```bash
uv venv
uv pip install --python .venv/bin/python -e .
```

On macOS/Linux, run from the repository root:

```bash
CLAWCODEX_CONFIG_DIR="$PWD/demos/parallel_search" .venv/bin/clawcodex mcp list
CLAWCODEX_CONFIG_DIR="$PWD/demos/parallel_search" .venv/bin/python -m demos.parallel_search
```

On Windows PowerShell:

```powershell
$env:CLAWCODEX_CONFIG_DIR = "$PWD/demos/parallel_search"
.venv\Scripts\clawcodex.exe mcp list
.venv\Scripts\python.exe -m demos.parallel_search
Remove-Item Env:CLAWCODEX_CONFIG_DIR
```

The list command should include `parallel-search`. The Python example loads
`config.json` through ClawCodex's normal MCP configuration loader, discovers
`web_search` and `web_fetch`, then prints search results for the Python asyncio
documentation and excerpts from its official page. It reuses one session ID for
both calls and closes the connection on success or failure. It calls tools
directly without a model or API key; it does not run an agent conversation.
Network or rate-limit errors are reported as failures. The configuration sends a
project User-Agent and no Authorization header.

The environment override selects this demo's configuration directory only for
these commands. To add the server to your usual configuration, merge the
`parallel-search` entry from this demo's `mcpServers` object into your existing
`~/.clawcodex/config.json` (or your configured directory). Keep your existing
provider settings and other MCP entries. Do not overwrite the whole file.
This example leaves the built-in WebSearch tool and default model provider
unchanged.
