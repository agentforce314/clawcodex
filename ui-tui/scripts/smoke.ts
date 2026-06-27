/**
 * Connectivity smoke: drive one full turn (incl. a permission round-trip)
 * against a running agent-server, using the real client runtime. Prints a
 * JSON summary and exits 0 on success.
 *
 *   bun run scripts/smoke.ts http://127.0.0.1:<port>
 */
import { createSession, DirectConnectClient } from '../src/client.js';
import { blocksToText } from '../src/protocol.js';

const url = process.argv[2];
if (!url) {
  console.error('usage: bun run scripts/smoke.ts <http://host:port>');
  process.exit(2);
}

const got: any[] = [];
let perms = 0;

const info = await createSession(url, '/tmp');
const client: DirectConnectClient = new DirectConnectClient(info, {
  onMessage: (m) => got.push(m),
  onPermissionRequest: (_req, id) => {
    perms += 1;
    client.respondPermission(id, 'allow');
  },
});
await client.connect();
client.sendPrompt('hello');

const start = Date.now();
while (Date.now() - start < 8000) {
  if (got.some((m) => m.type === 'result')) break;
  await new Promise((r) => setTimeout(r, 50));
}
client.close();

const result = got.find((m) => m.type === 'result');
const assistant = got.filter((m) => m.type === 'assistant');
const assistantText = assistant.map((a) => blocksToText(a.message?.content)).join(' ');
const ok =
  !!result &&
  result.subtype === 'success' &&
  perms >= 1 &&
  assistantText.includes('done');

console.log(
  JSON.stringify({
    result: result?.subtype ?? null,
    permissionRoundTrips: perms,
    assistantCount: assistant.length,
    assistantText,
    ok,
  }),
);
process.exit(ok ? 0 : 1);
