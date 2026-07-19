const test = require('node:test');
const assert = require('node:assert/strict');
const { parseToolInput, toolDisplayName, toolIcon } = require('./messageParser');

test('parseToolInput prefers command, then paths, then query, then JSON', () => {
  assert.equal(parseToolInput({ command: 'ls -la', file_path: '/x' }), 'ls -la');
  assert.equal(parseToolInput({ file_path: '/a/b.txt' }), '/a/b.txt');
  assert.equal(parseToolInput({ path: '/c' }), '/c');
  assert.equal(parseToolInput({ query: 'find me' }), 'find me');
  assert.equal(parseToolInput({ other: 1 }), JSON.stringify({ other: 1 }, null, 2));
  assert.equal(parseToolInput(null), '');
  assert.equal(parseToolInput('plain'), 'plain');
});

test('toolDisplayName maps known tools and falls back to the raw name', () => {
  assert.equal(toolDisplayName('Bash'), 'Terminal');
  assert.equal(toolDisplayName('Edit'), 'Edit File');
  assert.equal(toolDisplayName('Agent'), 'Sub-agent');
  assert.equal(toolDisplayName('ExitPlanMode'), 'Plan Approval');
  assert.equal(toolDisplayName('SomethingNew'), 'SomethingNew');
  assert.equal(toolDisplayName(undefined), 'Tool');
});

test('toolIcon maps known tools and falls back to the wrench', () => {
  assert.equal(toolIcon('Bash'), '\u{1F4BB}');
  assert.equal(toolIcon('WebSearch'), '\u{1F310}');
  assert.equal(toolIcon('Unmapped'), '\u{1F527}');
});
