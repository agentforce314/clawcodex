# Setup Guide

Detailed installation and configuration for **clawcodex**. For a quick overview, see the [Quick Start](../../README.md#-quick-start) in the main README — this guide expands on each step and adds a provider reference and troubleshooting.

## Prerequisites

- **Python 3.10+** (3.11 recommended)
- **git**
- **[uv](https://github.com/astral-sh/uv#installation)** (recommended)
- An API key for at least one supported provider: Anthropic, OpenAI, Z.ai (GLM), MiniMax, OpenRouter, or DeepSeek

## 1. Clone and install

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex

# Create and activate a virtual environment (uv recommended)
uv venv --python 3.11
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the package with its entry point
uv pip install -e ".[dev]"
```

Confirm the CLI is available:

```bash
clawcodex --help
```

If you'd rather not install the entry point, you can always run `python -m src.cli` in place of `clawcodex`.

## 2. Configure a provider

clawcodex reads its configuration from `~/.clawcodex/config.json`. Create it interactively or by hand.

### Option A — Interactive (recommended)

```bash
clawcodex login
```

This walks you through:

1. choosing a provider — `anthropic` / `openai` / `zai` / `minimax` / `openrouter` / `deepseek`
2. entering that provider's API key
3. optionally setting a custom base URL
4. optionally setting a default model
5. setting the chosen provider as the default

### Option B — Edit the config file directly

Create `~/.clawcodex/config.json` (only the providers you actually use are required):

```json
{
  "default_provider": "zai",
  "providers": {
    "anthropic": {
      "api_key": "your-api-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "zai": {
      "api_key": "your-api-key",
      "base_url": "https://api.z.ai/api/coding/paas/v4",
      "default_model": "glm-5.2"
    }
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

- **`default_provider`** — which provider block to use unless overridden with `--provider`.
- **`providers`** — one block per provider (`api_key`, `base_url`, `default_model`).
- **`env`** — secrets and environment values injected at startup (e.g. `TAVILY_API_KEY` for web search). Manage these with `clawcodex config`.

> **Secrets live in this single config file** (the `env` block and each provider's `api_key`) — clawcodex does **not** read `.env` files.

## 3. Provider reference

| Provider key | Base URL | Example model |
|---|---|---|
| `anthropic` | `https://api.anthropic.com` | `claude-sonnet-4-6` |
| `openai` | `https://api.openai.com/v1` | `gpt-5.4` |
| `zai` | `https://api.z.ai/api/coding/paas/v4` | `glm-5.2` (also `glm-5.1`) |
| `minimax` | `https://api.minimaxi.com/anthropic` | `MiniMax-M2.7` |
| `openrouter` | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-pro` |
| `deepseek` | `https://api.deepseek.com` | `deepseek-v4-pro` |

> **Z.ai (GLM):** clawcodex uses Z.ai's OpenAI-compatible GLM Coding Plan at `https://api.z.ai/api/coding/paas/v4`, serving `GLM-5.1` (stable) and `GLM-5.2` (preview). The legacy provider name `glm` is still accepted as an alias for `zai`. Get a key at <https://z.ai/>.

## 4. Run

```bash
clawcodex                  # start the interactive Ink TUI (same as: python -m src.cli)
clawcodex --help           # all flags: -p, --provider, --model, …
clawcodex --provider zai   # start this session with a specific provider
```

## Troubleshooting

- **`clawcodex: command not found`** — activate your virtualenv (`source .venv/bin/activate`), or run `python -m src.cli`.
- **`Unknown provider` / auth errors** — make sure the provider key in `~/.clawcodex/config.json` matches a block under `providers`, and that its `api_key` is set.
- **Wrong Python version** — clawcodex needs Python 3.10+. Check with `python --version`; recreate the venv with `uv venv --python 3.11` if needed.
- **Web search not working** — set `TAVILY_API_KEY` in the `env` block of the config (see above).

---

For full provider/model details and feature status, see the [main README](../../README.md) and [FEATURE_LIST.md](../../FEATURE_LIST.md).
