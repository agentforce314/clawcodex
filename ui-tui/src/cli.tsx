#!/usr/bin/env node
/**
 * `clawcodex-tui` — connect the Ink TUI to a running Python agent-server.
 *
 * Usage:
 *   clawcodex-tui <cc://host:port | http://host:port> [--token T] [--cwd DIR]
 *
 * Start the backend first:  `clawcodex agent-server`  (prints a cc:// URL).
 */
import { render } from 'ink';
import React from 'react';
import { App } from './App.js';
import { createSession } from './client.js';

interface Args {
  url: string | undefined;
  token: string | undefined;
  cwd: string;
}

function parseArgs(argv: string[]): Args {
  let url: string | undefined;
  let token: string | undefined = process.env['CLAWCODEX_TUI_TOKEN'];
  let cwd = process.cwd();
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i] as string;
    if (a === '--token') {
      token = argv[++i];
    } else if (a === '--cwd') {
      cwd = argv[++i] ?? cwd;
    } else if (!a.startsWith('-')) {
      url = a;
    }
  }
  return { url, token, cwd };
}

function toHttpUrl(url: string): string {
  if (url.startsWith('cc://')) return 'http://' + url.slice('cc://'.length);
  if (url.startsWith('cc+unix://')) {
    throw new Error('cc+unix:// (unix socket) is not supported by this client yet');
  }
  return url; // already http:// or https://
}

async function main(): Promise<void> {
  const { url, token, cwd } = parseArgs(process.argv.slice(2));
  if (!url) {
    console.error(
      'usage: clawcodex-tui <cc://host:port | http://host:port> [--token T] [--cwd DIR]',
    );
    process.exit(2);
    return;
  }

  let info;
  try {
    info = await createSession(toHttpUrl(url), cwd, token);
  } catch (err) {
    console.error(`failed to create session: ${(err as Error).message}`);
    process.exit(1);
    return;
  }

  const { waitUntilExit } = render(<App info={info} serverLabel={url} />);
  await waitUntilExit();
}

void main();
