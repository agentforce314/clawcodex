/**
 * Folder trust (the original's TrustDialog, inventory §6): remember which folders
 * the user has acknowledged as safe to operate in. Soft surface — the backend's
 * permission system still gates every action; this records consent + the first-run
 * notice. Stored at ~/.clawcodex/trusted.json.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'

const TRUST_FILE = join(homedir(), '.clawcodex', 'trusted.json')

function load(): string[] {
  try {
    const v = JSON.parse(readFileSync(TRUST_FILE, 'utf8'))
    return Array.isArray(v) ? v.map(String) : []
  } catch {
    return []
  }
}

function save(list: string[]): void {
  try {
    mkdirSync(dirname(TRUST_FILE), { recursive: true })
    writeFileSync(TRUST_FILE, JSON.stringify(list), 'utf8')
  } catch {
    /* best-effort */
  }
}

export function isTrusted(cwd: string): boolean {
  return load().includes(cwd)
}

export function trustFolder(cwd: string): void {
  const list = load()
  if (!list.includes(cwd)) {
    list.push(cwd)
    save(list)
  }
}

export function untrustFolder(cwd: string): void {
  save(load().filter((p) => p !== cwd))
}

/**
 * MCP server approval (the original's MCP server-trust dialog, §6): remember which
 * MCP servers the user has approved. Stored at ~/.clawcodex/trusted-mcp.json.
 */
const MCP_TRUST_FILE = join(homedir(), '.clawcodex', 'trusted-mcp.json')

function loadMcp(): string[] {
  try {
    const v = JSON.parse(readFileSync(MCP_TRUST_FILE, 'utf8'))
    return Array.isArray(v) ? v.map(String) : []
  } catch {
    return []
  }
}

export function isMcpTrusted(name: string): boolean {
  return loadMcp().includes(name)
}

export function trustMcp(name: string): void {
  const list = loadMcp()
  if (!list.includes(name)) {
    list.push(name)
    try {
      mkdirSync(dirname(MCP_TRUST_FILE), { recursive: true })
      writeFileSync(MCP_TRUST_FILE, JSON.stringify(list), 'utf8')
    } catch {
      /* best-effort */
    }
  }
}
