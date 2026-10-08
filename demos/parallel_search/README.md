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
documentation and excerpts from the first URL returned by search. It reuses one session ID for
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

## Use in an agent conversation

Merge the `parallel-search` server into your usual user configuration as above,
then launch `clawcodex` with your existing model provider. Ask it to use
`mcp__parallel-search__web_search`, fetch a URL from those results with
`mcp__parallel-search__web_fetch`, and explain the fetched page. Model inference
needs its own configured provider; anonymous Parallel access does not supply one.
The agent discovers deferred MCP schemas through `ToolSearch` before calling
them. Keep the normal permission prompts and approve each requested tool call only
when appropriate. Interactive project `.mcp.json` servers still use the existing approval rules;
the demo uses an explicit user-scope server. Invalid entries and policy notices
are shown during agent startup, and valid entries can still connect.

## Test the native path

Install development dependencies with `uv pip install --python .venv/bin/python -e ".[dev]"`.
The native regression uses the actual TUI/agent-server backend (`make_spawn_agent`),
MCP loader, discovery, HTTP SDK transport, tool registry, permission round trips,
and query loop. Its result-dependent controlled model fixture selects a URL from
search output and uses fetched content in its next response. It tests wiring,
including native `ToolSearch` discovery and denial before execution; it does not measure production model quality.
The default test uses a scripted HTTP peer and needs no network:

```bash
.venv/bin/python -m pytest tests/server/test_parallel_search_mcp.py -q
```

To exercise the same path against real anonymous Parallel search and fetch:

```bash
CLAWCODEX_TEST_PARALLEL_LIVE=1 .venv/bin/python -m pytest tests/server/test_parallel_search_mcp.py -q
```

The live run isolates user configuration and saved MCP tokens and removes API-key
environment variables. Only model inference is controlled; MCP calls use the real
endpoint. It checks outgoing URL/User-Agent, absence of Authorization, discovery,
explicit tool approvals, a result-selected fetch URL, and fetched content reaching
the subsequent provider response. Rate limits or network failures fail the live
check; the denial case is covered offline. No credentials are needed for this test.
