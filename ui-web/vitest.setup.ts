/**
 * jsdom gaps the components rely on. Kept to the minimum: anything a test
 * needs beyond this belongs in the test itself.
 */
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}

if (typeof globalThis.matchMedia === 'undefined') {
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

if (typeof Element.prototype.scrollTo === 'undefined') {
  Element.prototype.scrollTo = function scrollTo(): void {}
}

/**
 * Node 25 ships its own `localStorage` global, which shadows jsdom's inside
 * the test window as a plain object with none of the Storage methods (and
 * warns about `--localstorage-file`). Install a real in-memory Storage when
 * the one we got cannot store.
 */
if (typeof globalThis.localStorage?.getItem !== 'function') {
  const store = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: key => store.get(String(key)) ?? null,
    key: index => [...store.keys()][index] ?? null,
    removeItem: key => {
      store.delete(String(key))
    },
    setItem: (key, value) => {
      store.set(String(key), String(value))
    },
  }

  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })

  if (typeof window !== 'undefined') {
    Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
  }
}
