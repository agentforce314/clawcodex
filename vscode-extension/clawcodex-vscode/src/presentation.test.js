const test = require('node:test');
const assert = require('node:assert/strict');

function loadPresentation() {
  return require('./presentation');
}

test('truncateMiddle keeps the settings filename visible', () => {
  const { truncateMiddle } = loadPresentation();

  assert.equal(
    truncateMiddle('/Users/example/projects/clawcodex/workspace/.clawcodex/settings.json', 30),
    '.../settings.json',
  );
});

test('truncateMiddle keeps the filename visible for Windows-style paths', () => {
  const { truncateMiddle } = loadPresentation();

  assert.equal(
    truncateMiddle('C:\\Users\\example\\clawcodex\\workspace\\.clawcodex\\settings.json', 30),
    '...\\settings.json',
  );
});

test('buildActionModel disables workspace-root launch without a workspace', () => {
  const { buildActionModel } = loadPresentation();

  const model = buildActionModel({
    canLaunchInWorkspaceRoot: false,
    workspaceProfilePath: null,
  });

  assert.deepEqual(model.launchRoot, {
    id: 'launchRoot',
    label: 'Launch in Workspace Root',
    detail: 'Open a workspace folder to enable workspace-root launch',
    tone: 'neutral',
    disabled: true,
  });
});

test('buildActionModel hides project-settings action when no settings file exists', () => {
  const { buildActionModel } = loadPresentation();

  const model = buildActionModel({
    canLaunchInWorkspaceRoot: true,
    workspaceProfilePath: null,
  });

  assert.deepEqual(model.primary, {
    id: 'launch',
    label: 'Launch Clawcodex',
    detail: 'Use the resolved project-aware launch directory',
    tone: 'accent',
    disabled: false,
  });
  assert.equal(model.openProfile, null);
});

test('buildActionModel includes project-settings action when the file exists', () => {
  const { buildActionModel } = loadPresentation();

  const model = buildActionModel({
    canLaunchInWorkspaceRoot: true,
    workspaceProfilePath: 'C:\\Users\\example\\clawcodex\\workspace\\.clawcodex\\settings.json',
  });

  assert.deepEqual(model.openProfile, {
    id: 'openProfile',
    label: 'Open Project Settings',
    detail: 'Inspect ...\\settings.json',
    tone: 'neutral',
    disabled: false,
  });
});

function createStatus(overrides = {}) {
  return {
    installed: true,
    executable: 'clawcodex',
    launchCommand: 'clawcodex --verbose',
    terminalName: 'Clawcodex',
    workspaceFolder: '/workspace/clawcodex',
    workspaceSourceLabel: 'active editor workspace',
    launchCwd: '/workspace/clawcodex',
    launchCwdLabel: '/workspace/clawcodex',
    canLaunchInWorkspaceRoot: true,
    profileStatusLabel: 'Found',
    profileStatusHint: '/workspace/clawcodex/.clawcodex/settings.json',
    workspaceProfilePath: '/workspace/clawcodex/.clawcodex/settings.json',
    providerState: {
      label: 'DeepSeek',
      detail: 'deepseek-chat',
      source: 'config',
    },
    providerSourceLabel: 'clawcodex config',
    ...overrides,
  };
}

test('buildControlCenterViewModel keeps header badges and summary cards non-redundant', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus());
  const headerKeys = new Set(viewModel.headerBadges.map(badge => badge.key));
  const summaryKeys = new Set(viewModel.summaryCards.map(card => card.key));

  assert.deepEqual([...headerKeys].sort(), ['profileStatus', 'provider', 'runtime']);
  assert.deepEqual([...summaryKeys].sort(), ['launchCommand', 'launchCwd', 'workspace']);

  for (const key of headerKeys) {
    assert.equal(summaryKeys.has(key), false);
  }
});

test('buildControlCenterViewModel uses stable semantic tones for badges and actions', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus({
    installed: false,
    profileStatusLabel: 'Invalid',
    providerState: {
      label: 'Unknown',
      detail: 'no clawcodex config found',
      source: 'unknown',
    },
    providerSourceLabel: 'unknown',
  }));

  assert.deepEqual(viewModel.headerBadges, [
    {
      key: 'runtime',
      label: 'Runtime',
      value: 'Missing',
      tone: 'critical',
    },
    {
      key: 'provider',
      label: 'Provider',
      value: 'Unknown',
      tone: 'warning',
    },
    {
      key: 'profileStatus',
      label: 'Project settings',
      value: 'Invalid',
      tone: 'warning',
    },
  ]);

  assert.equal(viewModel.actions.primary.tone, 'accent');
  assert.equal(viewModel.actions.launchRoot.tone, 'neutral');
});

test('buildControlCenterViewModel uses a concise project summary before full path detail', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus());

  assert.deepEqual(viewModel.detailSections, [
    {
      title: 'Project',
      rows: [
        {
          key: 'workspace',
          label: 'Workspace folder',
          summary: 'clawcodex',
          detail: '/workspace/clawcodex · active editor workspace',
        },
        {
          key: 'profileStatus',
          label: 'Project settings',
          summary: 'Found',
          detail: '/workspace/clawcodex/.clawcodex/settings.json',
          tone: 'neutral',
        },
      ],
    },
    {
      title: 'Runtime',
      rows: [
        {
          key: 'runtime',
          label: 'Clawcodex executable',
          summary: 'Installed',
          detail: 'clawcodex',
          tone: 'positive',
        },
        {
          key: 'provider',
          label: 'Detected provider',
          summary: 'DeepSeek',
          detail: 'deepseek-chat · clawcodex config',
          tone: 'neutral',
        },
      ],
    },
  ]);
});

test('buildControlCenterViewModel keeps launch command only in summary cards', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus());

  assert.deepEqual(viewModel.summaryCards.find(card => card.key === 'launchCommand'), {
    key: 'launchCommand',
    label: 'Launch command',
    value: 'clawcodex --verbose',
    detail: 'Integrated terminal: Clawcodex',
  });

  assert.equal(
    viewModel.detailSections.some(section => section.rows.some(row => row.key === 'launchCommand')),
    false,
  );
});

test('buildControlCenterViewModel keeps unknown provider detail honest', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus({
    providerState: {
      label: 'Unknown',
      detail: 'no clawcodex config found',
      source: 'unknown',
    },
    providerSourceLabel: 'unknown',
  }));

  assert.deepEqual(viewModel.detailSections[1].rows.find(row => row.key === 'provider'), {
    key: 'provider',
    label: 'Detected provider',
    summary: 'Unknown',
    detail: 'no clawcodex config found',
    tone: 'warning',
  });
});

test('buildControlCenterViewModel appends the source label to configured provider detail', () => {
  const { buildControlCenterViewModel } = loadPresentation();

  const viewModel = buildControlCenterViewModel(createStatus({
    providerState: {
      label: 'Anthropic',
      detail: 'claude-opus-4-6 · key from ANTHROPIC_API_KEY',
      source: 'env',
    },
    providerSourceLabel: 'environment',
  }));

  assert.deepEqual(viewModel.detailSections[1].rows.find(row => row.key === 'provider'), {
    key: 'provider',
    label: 'Detected provider',
    summary: 'Anthropic',
    detail: 'claude-opus-4-6 · key from ANTHROPIC_API_KEY · environment',
    tone: 'neutral',
  });
});

test('buildControlCenterViewModel carries forward the existing action model', () => {
  const { buildControlCenterViewModel, buildActionModel } = loadPresentation();

  const status = createStatus();
  const viewModel = buildControlCenterViewModel(status);

  assert.deepEqual(viewModel.actions, buildActionModel(status));
});
