/**
 * Minimal functional `vscode` module stub for `node --test` runs.
 *
 * The EventEmitter is real (fire → listeners) — processManager tests depend
 * on actual event dispatch, not a no-op. Everything UI-facing is inert.
 */

class EventEmitter {
  constructor() {
    this._listeners = new Set();
    this.event = (listener) => {
      this._listeners.add(listener);
      return { dispose: () => this._listeners.delete(listener) };
    };
  }

  fire(payload) {
    for (const listener of [...this._listeners]) {
      listener(payload);
    }
  }

  dispose() {
    this._listeners.clear();
  }
}

class Uri {
  constructor(value) {
    this._value = String(value);
  }

  static parse(value) {
    return new Uri(value);
  }

  static file(value) {
    const uri = new Uri(`file://${value}`);
    uri.fsPath = String(value);
    return uri;
  }

  toString() {
    return this._value;
  }
}

const disposable = () => ({ dispose() {} });

module.exports = {
  EventEmitter,
  Uri,
  ViewColumn: { Active: 1, Beside: -2 },
  StatusBarAlignment: { Left: 1, Right: 2 },
  ConfigurationTarget: { Global: 1, Workspace: 2 },
  workspace: {
    workspaceFolders: [],
    getConfiguration: () => ({
      get: (_key, fallback) => fallback,
    }),
    getWorkspaceFolder: () => null,
    openTextDocument: async () => ({}),
    registerTextDocumentContentProvider: disposable,
    createFileSystemWatcher: () => ({
      onDidCreate: disposable,
      onDidChange: disposable,
      onDidDelete: disposable,
      dispose() {},
    }),
    onDidChangeConfiguration: disposable,
    onDidChangeWorkspaceFolders: disposable,
  },
  window: {
    activeTextEditor: null,
    createWebviewPanel: () => ({
      webview: { postMessage() {}, onDidReceiveMessage: disposable },
      onDidDispose: disposable,
      reveal() {},
      dispose() {},
    }),
    registerWebviewViewProvider: disposable,
    createStatusBarItem: () => ({
      show() {},
      hide() {},
      dispose() {},
      text: '',
      tooltip: '',
      command: '',
    }),
    createTerminal: () => ({ show() {}, sendText() {}, dispose() {} }),
    showInformationMessage: async () => undefined,
    showWarningMessage: async () => undefined,
    showErrorMessage: async () => undefined,
    showTextDocument: async () => undefined,
    showQuickPick: async () => undefined,
    onDidChangeActiveTextEditor: disposable,
  },
  env: {
    openExternal: async () => true,
    clipboard: { writeText: async () => undefined },
  },
  commands: {
    registerCommand: disposable,
    executeCommand: async () => undefined,
  },
};
