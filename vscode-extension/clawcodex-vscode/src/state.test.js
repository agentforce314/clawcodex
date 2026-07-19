const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  chooseLaunchWorkspace,
  describeProviderState,
  findCommandPath,
  parseClawcodexConfig,
  parseProjectSettings,
  resolveCommandCheckPath,
  resolveConfigDir,
} = require('./state');

test('chooseLaunchWorkspace prefers the active workspace folder', () => {
  assert.deepEqual(
    chooseLaunchWorkspace({
      activeWorkspacePath: '/repo-b',
      workspacePaths: ['/repo-a', '/repo-b'],
    }),
    { workspacePath: '/repo-b', source: 'active-workspace' },
  );
});

test('chooseLaunchWorkspace falls back to the first workspace folder', () => {
  assert.deepEqual(
    chooseLaunchWorkspace({
      activeWorkspacePath: null,
      workspacePaths: ['/repo-a', '/repo-b'],
    }),
    { workspacePath: '/repo-a', source: 'first-workspace' },
  );
});

test('resolveConfigDir honors CLAWCODEX_CONFIG_DIR and never falls back to ~/.claude', () => {
  assert.equal(resolveConfigDir({ CLAWCODEX_CONFIG_DIR: '/custom/dir' }), '/custom/dir');
  const fallback = resolveConfigDir({});
  assert.equal(fallback, path.join(os.homedir(), '.clawcodex'));
  assert.ok(!fallback.includes('.claude' + path.sep) && !fallback.endsWith('.claude'));
});

test('parseClawcodexConfig returns null for invalid JSON', () => {
  assert.equal(parseClawcodexConfig('{bad json}'), null);
});

test('parseClawcodexConfig returns null for non-object documents', () => {
  assert.equal(parseClawcodexConfig('[1, 2, 3]'), null);
  assert.equal(parseClawcodexConfig('"provider"'), null);
});

test('parseClawcodexConfig extracts default provider and providers map', () => {
  assert.deepEqual(
    parseClawcodexConfig(JSON.stringify({
      default_provider: 'deepseek',
      providers: {
        deepseek: { api_key: 'sk-x', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat' },
      },
      session: {},
    })),
    {
      defaultProvider: 'deepseek',
      providers: {
        deepseek: { api_key: 'sk-x', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat' },
      },
    },
  );
});

test('parseProjectSettings accepts any JSON object and rejects the rest', () => {
  assert.deepEqual(parseProjectSettings('{"permissions": {}}'), { permissions: {} });
  assert.equal(parseProjectSettings('[1]'), null);
  assert.equal(parseProjectSettings('not json'), null);
});

test('resolveCommandCheckPath resolves workspace-relative executables', () => {
  assert.equal(
    resolveCommandCheckPath('./node_modules/.bin/clawcodex', '/repo'),
    path.resolve('/repo', './node_modules/.bin/clawcodex'),
  );
});

test('resolveCommandCheckPath leaves bare commands alone', () => {
  assert.equal(resolveCommandCheckPath('clawcodex', '/repo'), null);
});

test('findCommandPath treats shell-like input as a literal executable name', t => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-command-'));
  t.after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  const commandName = process.platform === 'win32'
    ? 'clawcodex & whoami'
    : 'clawcodex && whoami';
  const executableName = process.platform === 'win32'
    ? `${commandName}.cmd`
    : commandName;
  const executablePath = path.join(tempDir, executableName);

  fs.writeFileSync(executablePath, process.platform === 'win32' ? '@echo off\r\n' : '#!/bin/sh\n');
  if (process.platform !== 'win32') {
    fs.chmodSync(executablePath, 0o755);
  }

  const resolvedPath = findCommandPath(commandName, {
    cwd: null,
    env: {
      PATH: tempDir,
      PATHEXT: '.CMD;.EXE',
    },
    platform: process.platform,
  });

  assert.ok(resolvedPath);
  assert.equal(resolvedPath.toLowerCase(), executablePath.toLowerCase());
});

function makeConfig(overrides = {}) {
  return {
    defaultProvider: 'anthropic',
    providers: {
      anthropic: { api_key: 'sk-ant', base_url: '', default_model: 'claude-opus-4-6' },
      deepseek: { api_key: '', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat' },
      ...((overrides.providers) || {}),
    },
    ...overrides,
  };
}

test('describeProviderState reports the config default provider with a configured key', () => {
  assert.deepEqual(
    describeProviderState({ config: makeConfig(), env: {} }),
    {
      label: 'Anthropic',
      detail: 'claude-opus-4-6',
      source: 'config',
    },
  );
});

test('describeProviderState lets the VS Code setting override the config default', () => {
  const state = describeProviderState({
    config: makeConfig(),
    env: { DEEPSEEK_API_KEY: 'sk-ds' },
    settingsProvider: 'deepseek',
  });
  assert.equal(state.label, 'DeepSeek');
  assert.equal(state.source, 'setting');
  assert.match(state.detail, /deepseek-chat/);
});

test('describeProviderState reports env-sourced keys for unkeyed providers', () => {
  const config = makeConfig({ defaultProvider: 'deepseek' });
  assert.deepEqual(
    describeProviderState({ config, env: { DEEPSEEK_API_KEY: 'sk-ds' } }),
    {
      label: 'DeepSeek',
      detail: 'deepseek-chat · key from DEEPSEEK_API_KEY',
      source: 'env',
    },
  );
});

test('describeProviderState flags a provider with no detectable key', () => {
  const config = makeConfig({ defaultProvider: 'deepseek' });
  const state = describeProviderState({ config, env: {} });
  assert.equal(state.source, 'config');
  assert.match(state.detail, /no API key detected/);
});

test('describeProviderState prefers the settings model in the detail line', () => {
  const state = describeProviderState({
    config: makeConfig(),
    env: {},
    settingsModel: 'claude-sonnet-5',
  });
  assert.equal(state.detail, 'claude-sonnet-5');
});

test('describeProviderState stays honest when nothing is configured', () => {
  assert.deepEqual(
    describeProviderState({ config: null, env: {} }),
    {
      label: 'Unknown',
      detail: 'no clawcodex config found',
      source: 'unknown',
    },
  );
});

test('describeProviderState stays honest when the config has no default provider', () => {
  assert.deepEqual(
    describeProviderState({ config: { defaultProvider: null, providers: {} }, env: {} }),
    {
      label: 'Unknown',
      detail: 'no default provider configured',
      source: 'unknown',
    },
  );
});
