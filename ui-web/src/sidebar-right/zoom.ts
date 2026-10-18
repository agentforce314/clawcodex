export type PreviewZoom = 'fit' | number

export const clampZoom = (value: number): number =>
  Number.isFinite(value) ? Math.max(0.25, Math.min(4, value)) : 1
