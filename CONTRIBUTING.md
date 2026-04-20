# Contributing to Claw Codex

Thank you for your interest in contributing to Claw Codex! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Coding Standards](#coding-standards)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/0/code_of_conduct/). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Development Setup

### Prerequisites

- Python 3.10 or higher (3.11 recommended for local development)
- `uv` (recommended) or `pip`
- git
- A valid API key from at least one provider (Anthropic, OpenAI, or GLM)

### Initial Setup

1. **Fork and clone the repository**

```bash
# Fork the repo on GitHub, then:
git clone https://github.com/YOUR_USERNAME/clawcodex.git
cd clawcodex
```

2. **Create a virtual environment**

```bash
uv venv --python 3.11
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**

```bash
uv pip install -r requirements.txt
uv pip install -e ".[dev]"
```

4. **Install development tools**

```bash
uv pip install black isort mypy pytest
```

5. **Configure your API key**

```bash
python -m src.cli login
# or use environment variables such as GLM_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
```

6. **Run tests to verify setup**

```bash
python -m pytest tests/ -q
```

## Project Structure

```
clawcodex/
├── src/                    # Source code
│   ├── providers/         # LLM provider implementations
│   ├── repl/              # Interactive REPL
│   ├── agent/             # Session management
│   ├── skills/            # SKILL.md loading and creation
│   ├── tool_system/       # Tool registry, loop, validation
│   ├── config.py          # Configuration management
│   └── cli.py             # CLI commands
├── tests/                 # Test files
├── .github/               # GitHub workflows and templates
├── requirements.txt       # Python dependencies
├── pyproject.toml         # Project metadata
└── README.md              # Project overview
```

### Key Modules

- **`src/providers/`**: LLM provider implementations
  - `base.py`: Abstract base class for providers
  - `anthropic_provider.py`: Anthropic/Claude integration
  - `openai_provider.py`: OpenAI/GPT integration
  - `glm_provider.py`: GLM/Zhipu AI integration

- **`src/repl/`**: Interactive REPL implementation
  - `core.py`: Main REPL logic

- **`src/agent/`**: Session and conversation management
  - `session.py`: Session persistence
  - `conversation.py`: Message history

- **`src/config.py`**: Configuration management
  - Load/save configuration
  - API key management
  - Provider settings

- **`src/cli.py`**: CLI command implementations

## Coding Standards

### Python Style Guide

We follow PEP 8 with a few modifications:

- **Line length**: 88 characters (Black default)
- **Quotes**: Double quotes for strings, single quotes for dict keys
- **Imports**: Sorted with isort

### Type Hints

**All public functions must have type hints.**

```python
# Good
def get_provider_config(provider: str) -> dict[str, Any]:
    """Get configuration for a specific provider."""
    pass

# Bad
def get_provider_config(provider):
    pass
```

### Docstrings

Use Google-style docstrings for all public functions and classes:

```python
def calculate_cost(tokens: int, model: str) -> float:
    """Calculate the cost for a given number of tokens.

    Args:
        tokens: Number of tokens used.
        model: Model name to determine pricing.

    Returns:
        Total cost in USD.

    Raises:
        ValueError: If model is not recognized.
    """
    pass
```

### Code Formatting

We use **Black** for code formatting and **isort** for import sorting:

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Or both at once
black src/ tests/ && isort src/ tests/
```

### Type Checking

We use **mypy** for static type checking:

```bash
mypy src/
```

Aim for zero mypy errors in new code.

## Commit Guidelines

We follow [Conventional Commits](https://www.conventionalcommits.org/):

### Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Examples

```bash
feat(repl): add tab completion support
fix(provider): handle API rate limiting correctly
docs(readme): update installation instructions
test(config): add tests for API key encoding
```

### Commit Message Rules

1. Use imperative mood ("add feature" not "added feature")
2. Keep the first line under 72 characters
3. Reference issues and PRs in the footer
4. Write clear, descriptive commit messages

## Pull Request Process

### Before Submitting

1. **Create a feature branch**

```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes**

- Write clean, well-documented code
- Add tests for new functionality
- Ensure all tests pass

3. **Run quality checks**

```bash
# Format code
black src/ tests/
isort src/ tests/

# Type check
mypy src/

# Run tests
python -m pytest tests/ -q

# Test your changes manually
python -m src.cli
```

4. **Commit your changes**

```bash
git add .
git commit -m "feat: your feature description"
```

5. **Push to your fork**

```bash
git push origin feature/your-feature-name
```

### Submitting the PR

1. Go to GitHub and create a Pull Request
2. Fill in the PR template
3. Link any related issues
4. Request review from maintainers

### PR Requirements

- All tests must pass
- Code must be formatted with Black and isort
- No mypy errors
- New code must have type hints and docstrings
- New features must have tests
- Documentation must be updated (if applicable)

### Review Process

1. At least one maintainer must approve
2. All CI checks must pass
3. No merge conflicts
4. PR will be squashed and merged

## Testing

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -q

# Run specific test file
python -m pytest tests/test_tool_system_tools.py -q

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=html
```

### Writing Tests

We use **pytest** for testing:

```python
import pytest
from src.config import load_config, save_config


def test_load_config_default():
    """Test that load_config returns a valid config."""
    config = load_config()
    assert "providers" in config
    assert "default_provider" in config


def test_save_and_load_config(tmp_path):
    """Test config persistence."""
    config = {
        "default_provider": "glm",
        "providers": {
            "glm": {
                "api_key": "test_key",
                "base_url": "https://example.com",
                "default_model": "glm-4"
            }
        }
    }

    save_config(config)
    loaded = load_config()

    assert loaded["default_provider"] == "glm"
```

### Test Guidelines

1. **Test file naming**: `test_<module>.py`
2. **Test function naming**: `test_<description>`
3. **One test per concern**: Keep tests focused
4. **Use fixtures**: For common setup
5. **Test edge cases**: Not just happy paths
6. **Make tests independent**: No test should depend on another

## Questions?

If you have questions, feel free to:

- Open an issue on GitHub
- Start a discussion in the Discussions tab
- Reach out to maintainers

Thank you for contributing to Claw Codex!
