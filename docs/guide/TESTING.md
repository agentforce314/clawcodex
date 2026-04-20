# Testing Guide

This document describes the testing strategy and how to run tests for Claw Codex.

## Test Structure

```
tests/
├── test_agent_loop.py
├── test_claude_code_tool_parity.py
├── test_config.py
├── test_context_system.py
├── test_output_styles.py
├── test_porting_workspace.py
├── test_providers.py
├── test_repl.py
├── test_skills_system.py
└── test_tool_system_tools.py
```

## Running Tests

### Run All Tests

```bash
# Activate the project environment first
source .venv/bin/activate

# Using pytest (recommended)
python -m pytest tests/ -q

# Using unittest
python -m unittest discover -s tests -v
```

### Run Specific Test File

```bash
# Test configuration
python -m pytest tests/test_config.py -q

# Test providers
python -m pytest tests/test_providers.py -q

# Test REPL
python -m pytest tests/test_repl.py -q

# Test context and agent loop
python -m pytest tests/test_context_system.py tests/test_agent_loop.py -q
```

### Run Specific Test

```bash
# Run specific test by name
python -m pytest tests/test_config.py::TestLoadSaveConfig::test_save_and_load_config -v

# Run tests matching pattern
python -m pytest tests/ -k "api_key" -v
```

### Run with Coverage

```bash
# Install coverage tool
uv pip install pytest-cov

# Run tests with coverage report
python -m pytest tests/ --cov=src --cov-report=html

# Open coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

## Test Categories

### 1. Configuration Tests (`test_config.py`)

Tests for configuration management:

- **Config Path**: Test config file location and directory creation
- **Default Config**: Test default configuration values
- **API Key Encoding**: Test base64 encoding/decoding
- **Load/Save**: Test config persistence
- **Provider Config**: Test provider-specific settings
- **Set API Key**: Test API key configuration
- **Default Provider**: Test default provider management

**Example:**
```python
def test_save_and_load_config(self):
    """Test save and load roundtrip."""
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

### 2. Provider Tests (`test_providers.py`)

Tests for LLM provider implementations:

- **ChatMessage**: Test message dataclass
- **ChatResponse**: Test response dataclass
- **Anthropic Provider**: Test Claude integration
- **OpenAI Provider**: Test GPT integration
- **GLM Provider**: Test GLM integration
- **Provider Selection**: Test provider class retrieval

**Example:**
```python
@patch('anthropic.Anthropic')
def test_chat(self, mock_anthropic):
    """Test synchronous chat."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Hello!")]
    mock_response.model = "claude-sonnet-4-20250514"
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
    mock_response.stop_reason = "end_turn"

    # Test
    provider = AnthropicProvider(api_key="test_key")
    messages = [ChatMessage(role="user", content="Hi")]
    response = provider.chat(messages)

    assert response.content == "Hello!"
```

### 3. REPL Tests (`test_repl.py`)

Tests for interactive REPL:

- **REPL Initialization**: Test REPL setup
- **Command Handling**: Test slash commands
- **Session Management**: Test save/load sessions
- **Conversation**: Test message management
- **Multiline Mode**: Test multiline input

**Example:**
```python
def test_handle_command_multiline_toggle(self):
    """Test /multiline command."""
    repl = ClawcodexREPL(provider_name="glm")

    # Initially False
    assert repl.multiline_mode is False

    # Toggle to True
    repl.handle_command("/multiline")
    assert repl.multiline_mode is True

    # Toggle back to False
    repl.handle_command("/multiline")
    assert repl.multiline_mode is False
```

### 4. Porting Workspace Tests (`test_porting_workspace.py`)

Tests for porting completeness:

- **Manifest**: Test file and module counts
- **Query Engine**: Test summary generation
- **CLI Commands**: Test command execution
- **Parity Audit**: Test coverage verification
- **Session Tracking**: Test turn state

## Test Strategy

### Unit Tests
- Test individual functions and classes
- Mock external dependencies (API calls)
- Fast execution (< 1 second per test)
- Independent and isolated

### Integration Tests
- Test component interactions
- Use real API keys only in CI/CD (with secrets)
- Longer execution time
- May require cleanup

### End-to-End Checks
- Test complete workflows in the real REPL
- Currently performed manually for provider login, REPL interaction, skills, and context behavior
- Useful when validating prompt behavior or CLI UX changes

## Writing Tests

### Test Naming Convention

```python
def test_<what_is_being_tested>(self):
    """Test description."""
    pass
```

### Test Structure (AAA Pattern)

```python
def test_feature(self):
    # Arrange - Set up test data
    config = {"default_provider": "glm"}

    # Act - Execute the code
    save_config(config)
    loaded = load_config()

    # Assert - Verify results
    assert loaded["default_provider"] == "glm"
```

### Best Practices

1. **One assertion per test** (when practical)
2. **Use descriptive test names**
3. **Test edge cases and error conditions**
4. **Keep tests independent**
5. **Use fixtures for common setup**
6. **Mock external dependencies**

### Example Test with Mock

```python
@patch('src.providers.openai.OpenAI')
def test_openai_chat(self, mock_openai):
    """Test OpenAI chat with mock."""
    # Arrange
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Response"
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai.return_value = mock_client

    # Act
    provider = OpenAIProvider(api_key="test")
    response = provider.chat([ChatMessage(role="user", content="Hi")])

    # Assert
    self.assertEqual(response.content, "Response")
```

## Test Coverage

### Current Coverage

- Coverage changes as features evolve
- Use the commands below to generate up-to-date local reports
- Prefer focusing on critical paths rather than preserving a stale percentage in docs

### Coverage Goals

- Minimum: 80%
- Target: 90%+
- Critical paths: 100%

### Check Coverage

```bash
# Generate coverage report
python -m pytest tests/ --cov=src --cov-report=term-missing

# View missing lines
python -m pytest tests/ --cov=src --cov-report=term-missing | grep "TOTAL"
```

## Continuous Integration

Tests run automatically on:

- Pull requests
- Commits to main branch
- Releases

### CI Configuration

Tests are configured in `.github/workflows/` (if exists):

```yaml
- name: Run tests
  run: python -m pytest tests/ -q --cov=src
```

## Test Data

### Fixtures

Common test data is stored in fixtures:

```python
# In test file
def setUp(self):
    """Set up test fixtures."""
    self.temp_dir = tempfile.mkdtemp()
    self.config_dir = Path(self.temp_dir) / ".clawcodex"
    self.config_dir.mkdir(parents=True, exist_ok=True)
```

### Test Sessions

Test sessions are created in temporary directories and cleaned up after tests.

## Troubleshooting

### Common Issues

1. **Import errors**: Ensure `src/` is in Python path
   ```bash
   export PYTHONPATH="${PYTHONPATH}:$(pwd)"
   ```

2. **API key errors**: Tests should use mocks, not real API keys

3. **Permission errors**: Check file permissions in test directories

4. **Slow tests**: Check for network calls (should be mocked)

### Debug Tests

```bash
# Run with verbose output
python -m pytest tests/ -v -s

# Run with pdb debugger
python -m pytest tests/ --pdb

# Run specific failing test with output
python -m pytest tests/test_config.py::TestClassName::test_name -v -s
```

## Performance Tests

```bash
# Run performance benchmarks
python -m pytest tests/ --benchmark-only
```

## Security Tests

- API keys are never logged
- Config files use encoded keys
- Secrets are not in git
- `.env` is in `.gitignore`

## Contributing Tests

When adding new features:

1. **Write tests first** (TDD approach)
2. **Test edge cases**
3. **Document test purpose**
4. **Ensure all tests pass**
5. **Check coverage**

## Test Maintenance

- Review and update tests when:
  - Adding new features
  - Fixing bugs
  - Refactoring code
  - Updating dependencies

## Summary

Good testing practices ensure:

- Code reliability
- Regression prevention
- Documentation of behavior
- Confidence in refactoring
- Better code design

**Run tests before every commit!**

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```
