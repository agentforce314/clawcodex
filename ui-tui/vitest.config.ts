import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    exclude: ['dist/**', 'node_modules/**'],
    // The suite contains CPU-sensitive cursor-layout and child-process timing
    // regressions. Unbounded file workers oversubscribe developer/CI hosts and
    // turn those assertions into load-dependent failures.
    maxWorkers: 4
  }
})
