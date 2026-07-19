const fs = require('fs');
const os = require('os');
const path = require('path');

// Display names for the providers clawcodex ships specs for. Anything not
// listed renders with a capitalized fallback of its config key.
const PROVIDER_LABELS = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  zai: 'Z.ai',
  minimax: 'MiniMax',
  openrouter: 'OpenRouter',
  moonshot: 'Moonshot',
  ollama: 'Ollama',
  together: 'Together AI',
  fireworks: 'Fireworks',
  huggingface: 'Hugging Face',
  meta: 'Meta',
  novita: 'Novita',
  deepinfra: 'DeepInfra',
  stepfun: 'StepFun',
  siliconflow: 'SiliconFlow',
  vllm: 'vLLM',
  sglang: 'SGLang',
};

// Env-var candidates per provider for display-only key detection — mirrors
// clawcodex's resolve_api_key fallback (configured key first, then env).
const PROVIDER_ENV_VARS = {
  anthropic: ['ANTHROPIC_API_KEY'],
  openai: ['OPENAI_API_KEY'],
  deepseek: ['DEEPSEEK_API_KEY'],
  gemini: ['GEMINI_API_KEY', 'GOOGLE_API_KEY'],
  zai: ['ZAI_API_KEY', 'Z_AI_API_KEY'],
  minimax: ['MINIMAX_API_KEY'],
  openrouter: ['OPENROUTER_API_KEY'],
  moonshot: ['MOONSHOT_API_KEY'],
  together: ['TOGETHER_API_KEY'],
  fireworks: ['FIREWORKS_API_KEY'],
  huggingface: ['HF_TOKEN', 'HUGGINGFACE_API_KEY'],
  meta: ['META_API_KEY', 'LLAMA_API_KEY'],
};

function asNonEmptyString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

/** First token of a launch command, honoring a quoted executable path. */
function getExecutableFromCommand(command) {
  const normalized = String(command || '').trim();
  if (!normalized) {
    return '';
  }

  const doubleQuotedMatch = normalized.match(/^"([^"]+)"/);
  if (doubleQuotedMatch) {
    return doubleQuotedMatch[1];
  }

  const singleQuotedMatch = normalized.match(/^'([^']+)'/);
  if (singleQuotedMatch) {
    return singleQuotedMatch[1];
  }

  return normalized.split(/\s+/)[0];
}

function chooseLaunchWorkspace({ activeWorkspacePath, workspacePaths }) {
  const activePath = asNonEmptyString(activeWorkspacePath);
  if (activePath) {
    return { workspacePath: activePath, source: 'active-workspace' };
  }

  const firstWorkspacePath = Array.isArray(workspacePaths)
    ? asNonEmptyString(workspacePaths[0])
    : null;

  if (firstWorkspacePath) {
    return { workspacePath: firstWorkspacePath, source: 'first-workspace' };
  }

  return { workspacePath: null, source: 'none' };
}

/**
 * Resolve the clawcodex user config directory: $CLAWCODEX_CONFIG_DIR or
 * ~/.clawcodex. Never falls back to ~/.claude — clawcodex deliberately does
 * not share state with a Claude Code install on the same machine.
 */
function resolveConfigDir(env = process.env) {
  const override = asNonEmptyString(env.CLAWCODEX_CONFIG_DIR);
  if (override) return override;
  return path.join(os.homedir(), '.clawcodex');
}

/**
 * Parse ~/.clawcodex/config.json into the slice the Control Center needs.
 * Returns null when the file is missing, unreadable, or not a JSON object —
 * the provider state then reports 'unknown' instead of guessing.
 */
function parseClawcodexConfig(raw) {
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null;
    }
    const providers =
      parsed.providers && typeof parsed.providers === 'object' && !Array.isArray(parsed.providers)
        ? parsed.providers
        : {};
    return {
      defaultProvider: asNonEmptyString(parsed.default_provider),
      providers,
    };
  } catch {
    return null;
  }
}

function readClawcodexConfig(env = process.env) {
  const configPath = path.join(resolveConfigDir(env), 'config.json');
  try {
    if (!fs.existsSync(configPath)) return { config: null, configPath };
    return { config: parseClawcodexConfig(fs.readFileSync(configPath, 'utf8')), configPath };
  } catch {
    return { config: null, configPath };
  }
}

/**
 * Parse a workspace `.clawcodex/settings.json`. Valid = any JSON object;
 * clawcodex project settings have no mandatory fields.
 */
function parseProjectSettings(raw) {
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function providerLabel(name) {
  const normalized = asNonEmptyString(name);
  if (!normalized) return 'Unknown';
  if (PROVIDER_LABELS[normalized]) return PROVIDER_LABELS[normalized];
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function envKeyFor(providerName, env) {
  const candidates = PROVIDER_ENV_VARS[providerName] || [
    `${String(providerName || '').toUpperCase().replace(/[^A-Z0-9]/g, '_')}_API_KEY`,
  ];
  for (const key of candidates) {
    if (asNonEmptyString(env?.[key])) return key;
  }
  return null;
}

/**
 * Conservative provider summary for the Control Center.
 *
 * Sources, in precedence order:
 *   'setting' — VS Code setting clawcodex.provider overrides the config default
 *   'config'  — config.json default_provider with a configured api_key
 *   'env'     — provider selected but keyed via an environment variable
 *   'unknown' — no config file / no provider — shown honestly, never guessed
 */
function describeProviderState({ config, env = {}, settingsProvider, settingsModel } = {}) {
  const overrideProvider = asNonEmptyString(settingsProvider);
  const providerName = overrideProvider || config?.defaultProvider || null;

  if (!providerName) {
    return {
      label: 'Unknown',
      detail: config ? 'no default provider configured' : 'no clawcodex config found',
      source: 'unknown',
    };
  }

  const providerCfg =
    config?.providers && typeof config.providers[providerName] === 'object'
      ? config.providers[providerName]
      : {};
  const model =
    asNonEmptyString(settingsModel) ||
    asNonEmptyString(providerCfg.default_model) ||
    null;
  const detailBase = model || asNonEmptyString(providerCfg.base_url) || 'provider default model';

  const hasConfiguredKey = Boolean(asNonEmptyString(providerCfg.api_key));
  const envKey = hasConfiguredKey ? null : envKeyFor(providerName, env);

  let source;
  if (overrideProvider) {
    source = 'setting';
  } else if (hasConfiguredKey) {
    source = 'config';
  } else if (envKey) {
    source = 'env';
  } else {
    source = 'config';
  }

  let detail = detailBase;
  if (!hasConfiguredKey && envKey) {
    detail = `${detailBase} · key from ${envKey}`;
  } else if (!hasConfiguredKey && !envKey) {
    detail = `${detailBase} · no API key detected`;
  }

  return {
    label: providerLabel(providerName),
    detail,
    source,
  };
}

function resolveCommandCheckPath(command, workspacePath) {
  const normalized = asNonEmptyString(command);
  if (!normalized) {
    return null;
  }

  if (!normalized.includes(path.sep) && !normalized.includes('/')) {
    return null;
  }

  if (path.isAbsolute(normalized)) {
    return normalized;
  }

  return workspacePath
    ? path.resolve(workspacePath, normalized)
    : path.resolve(normalized);
}

function getEnvValue(env, key) {
  if (!env || typeof env !== 'object') {
    return '';
  }

  const matchedKey = Object.keys(env).find(candidate => candidate.toUpperCase() === key);
  return matchedKey ? env[matchedKey] : '';
}

function canAccessExecutable(filePath, platform) {
  try {
    fs.accessSync(filePath, platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findCommandPath(command, options = {}) {
  const normalized = asNonEmptyString(command);
  if (!normalized) {
    return null;
  }

  const cwd = asNonEmptyString(options.cwd);
  const env = options.env || process.env;
  const platform = options.platform || process.platform;
  const hasPathSeparators = normalized.includes(path.sep) || normalized.includes('/');

  if (hasPathSeparators) {
    if (!path.isAbsolute(normalized) && !cwd) {
      return null;
    }

    const directPath = resolveCommandCheckPath(normalized, cwd);
    return directPath && canAccessExecutable(directPath, platform) ? directPath : null;
  }

  const pathValue = getEnvValue(env, 'PATH');
  if (!pathValue) {
    return null;
  }

  const pathExtValue = getEnvValue(env, 'PATHEXT');
  const hasExplicitExtension = Boolean(path.extname(normalized));
  const extensions = platform === 'win32'
    ? (hasExplicitExtension
        ? ['']
        : (pathExtValue || '.COM;.EXE;.BAT;.CMD')
            .split(';')
            .map(extension => extension.trim())
            .filter(Boolean))
    : [''];

  for (const directory of pathValue.split(path.delimiter)) {
    const baseDirectory = asNonEmptyString(directory);
    if (!baseDirectory) {
      continue;
    }

    for (const extension of extensions) {
      const candidatePath = path.join(baseDirectory, `${normalized}${extension}`);
      if (canAccessExecutable(candidatePath, platform)) {
        return candidatePath;
      }
    }
  }

  return null;
}

function isPathInsideWorkspace(filePath, workspacePath) {
  const normalizedFilePath = asNonEmptyString(filePath);
  const normalizedWorkspacePath = asNonEmptyString(workspacePath);
  if (!normalizedFilePath || !normalizedWorkspacePath) {
    return false;
  }

  const resolvedFilePath = path.resolve(normalizedFilePath);
  const resolvedWorkspacePath = path.resolve(normalizedWorkspacePath);
  const comparableFilePath = process.platform === 'win32'
    ? resolvedFilePath.toLowerCase()
    : resolvedFilePath;
  const comparableWorkspacePath = process.platform === 'win32'
    ? resolvedWorkspacePath.toLowerCase()
    : resolvedWorkspacePath;
  const relativePath = path.relative(comparableWorkspacePath, comparableFilePath);

  return relativePath === '' || (!relativePath.startsWith('..') && !path.isAbsolute(relativePath));
}

module.exports = {
  chooseLaunchWorkspace,
  describeProviderState,
  findCommandPath,
  getExecutableFromCommand,
  isPathInsideWorkspace,
  parseClawcodexConfig,
  parseProjectSettings,
  providerLabel,
  readClawcodexConfig,
  resolveCommandCheckPath,
  resolveConfigDir,
  PROVIDER_ENV_VARS,
};
