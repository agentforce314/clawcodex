import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('clawcodexDesktop', {
  getConnection: profile => ipcRenderer.invoke('clawcodex:connection', profile),
  revalidateConnection: () => ipcRenderer.invoke('clawcodex:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('clawcodex:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('clawcodex:gateway:ws-url', profile),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('clawcodex:window:openSession', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('clawcodex:window:openInstance'),
  claimAmbientCue: key => ipcRenderer.invoke('clawcodex:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('clawcodex:wake-indicator:get'),
    setState: state => ipcRenderer.send('clawcodex:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('clawcodex:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('clawcodex:wake-indicator:state', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('clawcodex:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('clawcodex:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('clawcodex:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('clawcodex:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('clawcodex:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('clawcodex:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('clawcodex:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('clawcodex:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('clawcodex:pet-overlay:control', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('clawcodex:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('clawcodex:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('clawcodex:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('clawcodex:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('clawcodex:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('clawcodex:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('clawcodex:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('clawcodex:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('clawcodex:quick-entry:shown', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('clawcodex:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('clawcodex:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('clawcodex:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('clawcodex:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('clawcodex:connection-config:test', payload),
  sshConfigHosts: () => ipcRenderer.invoke('clawcodex:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('clawcodex:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('clawcodex:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('clawcodex:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('clawcodex:connection-config:oauth-logout', remoteUrl),
  // ClawCodex Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('clawcodex:cloud:status'),
    login: () => ipcRenderer.invoke('clawcodex:cloud:login'),
    logout: () => ipcRenderer.invoke('clawcodex:cloud:logout'),
    discover: org => ipcRenderer.invoke('clawcodex:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('clawcodex:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('clawcodex:profile:get'),
    set: name => ipcRenderer.invoke('clawcodex:profile:set', name)
  },
  api: request => ipcRenderer.invoke('clawcodex:api', request),
  notify: payload => ipcRenderer.invoke('clawcodex:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('clawcodex:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('clawcodex:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('clawcodex:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('clawcodex:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('clawcodex:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('clawcodex:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('clawcodex:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('clawcodex:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('clawcodex:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('clawcodex:readClipboard'),
  saveImageFromUrl: url => ipcRenderer.invoke('clawcodex:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('clawcodex:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('clawcodex:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('clawcodex:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('clawcodex:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('clawcodex:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('clawcodex:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('clawcodex:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('clawcodex:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('clawcodex:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('clawcodex:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('clawcodex:keep-awake', on),
  setPreviewShortcutActive: active => ipcRenderer.send('clawcodex:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('clawcodex:openExternal', url),
  openPreviewInBrowser: url => ipcRenderer.invoke('clawcodex:openPreviewInBrowser', url),
  fetchLinkTitle: url => ipcRenderer.invoke('clawcodex:fetchLinkTitle', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('clawcodex:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('clawcodex:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('clawcodex:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('clawcodex:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('clawcodex:zoom:get'),
    setPercent: percent => ipcRenderer.send('clawcodex:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:zoom:changed', listener)

      return () => ipcRenderer.removeListener('clawcodex:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('clawcodex:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('clawcodex:logs:recent'),
  readDir: dirPath => ipcRenderer.invoke('clawcodex:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('clawcodex:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('clawcodex:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('clawcodex:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('clawcodex:fs:desktopPluginsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('clawcodex:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('clawcodex:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('clawcodex:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('clawcodex:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('clawcodex:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('clawcodex:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('clawcodex:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('clawcodex:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('clawcodex:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('clawcodex:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('clawcodex:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('clawcodex:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('clawcodex:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('clawcodex:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('clawcodex:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('clawcodex:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('clawcodex:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('clawcodex:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('clawcodex:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('clawcodex:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('clawcodex:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('clawcodex:git:review:shipInfo', repoPath),
      createPr: repoPath => ipcRenderer.invoke('clawcodex:git:review:createPr', repoPath)
    }
  },
  terminal: {
    cwd: id => ipcRenderer.invoke('clawcodex:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('clawcodex:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('clawcodex:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('clawcodex:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('clawcodex:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `clawcodex:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `clawcodex:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('clawcodex:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('clawcodex:close-preview-requested', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('clawcodex:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('clawcodex:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('clawcodex:open-updates', listener)

    return () => ipcRenderer.removeListener('clawcodex:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:deep-link', listener)

    return () => ipcRenderer.removeListener('clawcodex:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('clawcodex:deep-link-ready'),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:window-state-changed', listener)

    return () => ipcRenderer.removeListener('clawcodex:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('clawcodex:focus-session', listener)

    return () => ipcRenderer.removeListener('clawcodex:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:notification-action', listener)

    return () => ipcRenderer.removeListener('clawcodex:notification-action', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('clawcodex:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:backend-exit', listener)

    return () => ipcRenderer.removeListener('clawcodex:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('clawcodex:connection:applied', listener)

    return () => ipcRenderer.removeListener('clawcodex:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('clawcodex:power-resume', listener)

    return () => ipcRenderer.removeListener('clawcodex:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('clawcodex:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('clawcodex:power-battery', listener)

    return () => ipcRenderer.removeListener('clawcodex:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:boot-progress', listener)

    return () => ipcRenderer.removeListener('clawcodex:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('clawcodex:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('clawcodex:bootstrap:continue-local'),
  resetBootstrap: () => ipcRenderer.invoke('clawcodex:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('clawcodex:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('clawcodex:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('clawcodex:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('clawcodex:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('clawcodex:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('clawcodex:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('clawcodex:uninstall:summary'),
    run: mode => ipcRenderer.invoke('clawcodex:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('clawcodex:updates:check'),
    apply: opts => ipcRenderer.invoke('clawcodex:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('clawcodex:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('clawcodex:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('clawcodex:updates:progress', listener)

      return () => ipcRenderer.removeListener('clawcodex:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('clawcodex:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('clawcodex:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('clawcodex:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('clawcodex:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('clawcodex:found-in-page', listener)

    return () => ipcRenderer.removeListener('clawcodex:found-in-page', listener)
  }
})
