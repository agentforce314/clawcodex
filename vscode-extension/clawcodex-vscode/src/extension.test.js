const test = require('node:test');
const assert = require('node:assert/strict');

// `vscode` resolves to test/vscode-stub.js via the --require preload.
const {
  renderControlCenterHtml,
  resolveLaunchTargets,
  ClawcodexControlCenterProvider,
} = require('./extension');

function createStatus(overrides = {}) {
  return {
    installed: true,
    executable: 'clawcodex',
    launchCommand: 'clawcodex --verbose',
    terminalName: 'Clawcodex',
    workspaceFolder: '/workspace/clawcodex/very/long/path/example-project',
    workspaceSourceLabel: 'active editor workspace',
    launchCwd: '/workspace/clawcodex/very/long/path/example-project',
    launchCwdLabel: '/workspace/clawcodex/very/long/path/example-project',
    canLaunchInWorkspaceRoot: true,
    profileStatusLabel: 'Found',
    profileStatusHint: '/workspace/clawcodex/very/long/path/example-project/.clawcodex/settings.json',
    workspaceProfilePath: '/workspace/clawcodex/very/long/path/example-project/.clawcodex/settings.json',
    providerState: {
      label: 'DeepSeek',
      detail: 'deepseek-chat',
      source: 'config',
    },
    providerSourceLabel: 'clawcodex config',
    ...overrides,
  };
}

test('renderControlCenterHtml uses the clawcodex wordmark, status rail, and warm action hierarchy', () => {
  const html = renderControlCenterHtml(createStatus(), { nonce: 'test-nonce', platform: 'win32' });

  assert.match(html, /claw<span class="wordmark-accent">codex<\/span>/);
  assert.match(html, /class="status-rail"/);
  assert.match(html, /\.sunset-gradient\s*\{/);
  assert.match(html, /class="action-button primary" id="launch"/);
  assert.match(html, /class="action-button secondary" id="launchRoot"/);
  assert.match(
    html,
    /title="\/workspace\/clawcodex\/very\/long\/path\/example-project"[^>]*>\/workspace\/clawcodex\/very\/long\/path\/example-project<\//,
  );
});

test('renderControlCenterHtml shows explicit disabled and empty states when workspace data is missing', () => {
  const html = renderControlCenterHtml(
    createStatus({
      workspaceFolder: null,
      workspaceSourceLabel: 'no workspace open',
      launchCwd: null,
      launchCwdLabel: 'VS Code default terminal cwd',
      canLaunchInWorkspaceRoot: false,
      profileStatusLabel: 'No workspace',
      profileStatusHint: 'Open a workspace folder to detect project settings',
      workspaceProfilePath: null,
    }),
    { nonce: 'test-nonce', platform: 'linux' },
  );

  assert.match(
    html,
    /class="action-button secondary" id="launchRoot"[^>]*disabled[^>]*>[\s\S]*Open a workspace folder to enable workspace-root launch/,
  );
  assert.match(html, /No project settings yet/);
  assert.match(html, /Open a workspace folder to detect project settings/);
  assert.doesNotMatch(html, /id="openProfile"/);
});

test('renderControlCenterHtml links provider config instead of the Azure subsystem', () => {
  const html = renderControlCenterHtml(createStatus(), { nonce: 'test-nonce', platform: 'linux' });

  assert.match(html, /id="providerConfig"/);
  assert.match(html, /config\.json/);
  assert.doesNotMatch(html, /azure/i);
});

test('ClawcodexControlCenterProvider.getHtml supplies a nonce to the renderer', () => {
  const provider = new ClawcodexControlCenterProvider();

  assert.doesNotThrow(() => provider.getHtml(createStatus()));

  const html = provider.getHtml(createStatus());
  assert.match(html, /script-src 'nonce-[^']+'/);
  assert.match(html, /<script nonce="[^"]+">/);
  assert.doesNotMatch(html, /nonce-undefined/);
  assert.doesNotMatch(html, /<script nonce="undefined">/);
});

test('resolveLaunchTargets distinguishes project-aware launch from workspace-root launch', () => {
  assert.deepEqual(
    resolveLaunchTargets({
      activeFilePath: '/workspace/clawcodex/src/panels/control-center.js',
      workspacePath: '/workspace/clawcodex',
      workspaceSourceLabel: 'active editor workspace',
    }),
    {
      projectAwareCwd: '/workspace/clawcodex/src/panels',
      projectAwareCwdLabel: '/workspace/clawcodex/src/panels',
      projectAwareSourceLabel: 'active file directory',
      workspaceRootCwd: '/workspace/clawcodex',
      workspaceRootCwdLabel: '/workspace/clawcodex',
      launchActionsShareTarget: false,
      launchActionsShareTargetReason: null,
    },
  );
});

test('resolveLaunchTargets anchors relative launch commands to the workspace root', () => {
  assert.deepEqual(
    resolveLaunchTargets({
      executable: './bin/clawcodex',
      activeFilePath: '/workspace/clawcodex/src/panels/control-center.js',
      workspacePath: '/workspace/clawcodex',
      workspaceSourceLabel: 'active editor workspace',
    }),
    {
      projectAwareCwd: '/workspace/clawcodex',
      projectAwareCwdLabel: '/workspace/clawcodex',
      projectAwareSourceLabel: 'workspace root (required by relative launch command)',
      workspaceRootCwd: '/workspace/clawcodex',
      workspaceRootCwdLabel: '/workspace/clawcodex',
      launchActionsShareTarget: true,
      launchActionsShareTargetReason: 'relative-launch-command',
    },
  );
});

test('resolveLaunchTargets ignores active files outside the selected workspace', () => {
  assert.deepEqual(
    resolveLaunchTargets({
      executable: 'clawcodex',
      activeFilePath: '/tmp/notes/scratch.js',
      workspacePath: '/workspace/clawcodex',
      workspaceSourceLabel: 'first workspace folder',
    }),
    {
      projectAwareCwd: '/workspace/clawcodex',
      projectAwareCwdLabel: '/workspace/clawcodex',
      projectAwareSourceLabel: 'first workspace folder',
      workspaceRootCwd: '/workspace/clawcodex',
      workspaceRootCwdLabel: '/workspace/clawcodex',
      launchActionsShareTarget: true,
      launchActionsShareTargetReason: null,
    },
  );
});

test('renderControlCenterHtml restores landmark and heading semantics', () => {
  const html = renderControlCenterHtml(createStatus(), { nonce: 'test-nonce', platform: 'win32' });

  assert.match(html, /<main class="shell" aria-labelledby="control-center-title">/);
  assert.match(html, /<header class="hero">/);
  assert.match(html, /<h1 class="headline-title" id="control-center-title">/);
  assert.match(html, /<section class="modules" aria-label="Control center details">/);
  assert.match(html, /<h2 class="module-title" id="section-project">Project<\/h2>/);
  assert.match(html, /<section class="actions-layout" aria-label="Control center actions">/);
});

test('renderControlCenterHtml explains distinct launch targets when an active file directory is available', () => {
  const html = renderControlCenterHtml(
    createStatus({
      launchCwd: '/workspace/clawcodex/src/panels',
      launchCwdLabel: '/workspace/clawcodex/src/panels',
      launchCwdSourceLabel: 'active file directory',
      workspaceRootCwd: '/workspace/clawcodex',
      workspaceRootCwdLabel: '/workspace/clawcodex',
    }),
    { nonce: 'test-nonce', platform: 'linux' },
  );

  assert.match(html, /Starts beside the active file · \/workspace\/clawcodex\/src\/panels/);
  assert.match(html, /Always starts at the workspace root · \/workspace\/clawcodex/);
});

test('renderControlCenterHtml makes shared workspace-root launches explicit for relative commands', () => {
  const html = renderControlCenterHtml(
    createStatus({
      launchCwd: '/workspace/clawcodex',
      launchCwdLabel: '/workspace/clawcodex',
      launchCwdSourceLabel: 'workspace root (required by relative launch command)',
      workspaceRootCwd: '/workspace/clawcodex',
      workspaceRootCwdLabel: '/workspace/clawcodex',
      launchActionsShareTarget: true,
      launchActionsShareTargetReason: 'relative-launch-command',
    }),
    { nonce: 'test-nonce', platform: 'linux' },
  );

  assert.match(html, /Project-aware launch is anchored to the workspace root by the relative command · \/workspace\/clawcodex/);
  assert.match(html, /Same workspace-root target as Launch Clawcodex because the relative command resolves from the workspace root · \/workspace\/clawcodex/);
});

test('renderControlCenterHtml escapes hostile text and title values', () => {
  const html = renderControlCenterHtml(
    createStatus({
      launchCommand: '<img src=x onerror="boom()">',
      workspaceFolder: '"/><script>workspace()</script>',
      workspaceSourceLabel: 'active <b>workspace</b>',
      launchCwdLabel: '"><script>cwd()</script>',
      profileStatusHint: '<svg onload="profile()">',
      workspaceProfilePath: '"/><script>profile-path()</script>',
      providerState: {
        label: 'Provider "><img src=x onerror="label()">',
        detail: '<script>provider-detail()</script>',
        source: 'config',
      },
    }),
    { nonce: 'test-nonce', platform: 'linux' },
  );

  assert.match(html, /&lt;img src=x onerror=&quot;boom\(\)&quot;&gt;/);
  assert.match(html, /&quot;\/&gt;&lt;script&gt;workspace\(\)&lt;\/script&gt;/);
  assert.match(html, /active &lt;b&gt;workspace&lt;\/b&gt;/);
  assert.match(html, /&lt;svg onload=&quot;profile\(\)&quot;&gt;/);
  assert.match(html, /Provider &quot;&gt;&lt;img src=x onerror=&quot;label\(\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;provider-detail\(\)&lt;\/script&gt; · clawcodex config/);
  assert.doesNotMatch(html, /<script>workspace\(\)<\/script>/);
  assert.doesNotMatch(html, /<img src=x onerror="boom\(\)">/);
});
