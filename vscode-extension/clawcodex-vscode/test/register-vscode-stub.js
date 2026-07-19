/**
 * Preload hook (`node --require`) that serves the local stub whenever code
 * under test does `require('vscode')` — the real module only exists inside
 * the VS Code extension host.
 */

const Module = require('module');
const path = require('path');

const stubPath = path.join(__dirname, 'vscode-stub.js');
const originalResolve = Module._resolveFilename;

Module._resolveFilename = function resolveWithVscodeStub(request, ...rest) {
  if (request === 'vscode') {
    return stubPath;
  }
  return originalResolve.call(this, request, ...rest);
};
