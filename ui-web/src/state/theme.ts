/**
 * Theme selection: light, dark, or follow the OS.
 *
 * The resolved theme is stamped as `data-theme` on the root element, which is
 * the only thing the token sheet reads. `index.html` runs the same resolution
 * inline before the bundle loads so a cold start in dark never flashes white —
 * keep the storage key and the resolution rule in step with that snippet.
 */

import { atom } from 'nanostores'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'clawcodex.web.theme'

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)

    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    /* private mode */
  }

  return 'system'
}

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return systemPrefersDark() ? 'dark' : 'light'

  return preference
}

export const $themePreference = atom<ThemePreference>(readPreference())
export const $theme = atom<ResolvedTheme>(resolveTheme(readPreference()))

function stamp(theme: ResolvedTheme): void {
  document.documentElement.setAttribute('data-theme', theme)
  document.documentElement.toggleAttribute('data-boot-dark', theme === 'dark')
  // Native form controls and the scrollbar gutter follow this, not our tokens.
  document.documentElement.style.colorScheme = theme
}

export function setThemePreference(preference: ThemePreference): void {
  $themePreference.set(preference)

  try {
    window.localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    /* private mode: the choice holds for this page load */
  }

  const resolved = resolveTheme(preference)
  $theme.set(resolved)
  stamp(resolved)
}

/** Cycle light → dark → system, the order the appearance control walks. */
export function cycleTheme(): void {
  const order: ThemePreference[] = ['light', 'dark', 'system']
  const index = order.indexOf($themePreference.get())

  setThemePreference(order[(index + 1) % order.length] ?? 'system')
}

/** Install the OS listener and stamp the initial theme. Call once, at boot. */
export function installTheme(): () => void {
  stamp($theme.get())

  const query = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = () => {
    if ($themePreference.get() !== 'system') return

    const resolved = systemPrefersDark() ? 'dark' : 'light'
    $theme.set(resolved)
    stamp(resolved)
  }

  query.addEventListener('change', onChange)

  return () => {
    query.removeEventListener('change', onChange)
  }
}
