import css from './TextPreview.module.css'
import { clampZoom, type PreviewZoom } from './zoom.ts'

export function ZoomControls({ value, onChange, image = false }: {
  value: PreviewZoom; onChange: (zoom: PreviewZoom) => void; image?: boolean
}) {
  const scale = value === 'fit' ? 1 : value
  return <div className={css.zoomControls} role="group" aria-label="Preview zoom">
    <button type="button" className={css.tool} aria-label="Zoom out" disabled={scale <= 0.25}
      onClick={() => onChange(clampZoom(scale - 0.25))}>−</button>
    <select aria-label="Preview zoom level" value={value} onChange={event => onChange(event.target.value === 'fit' ? 'fit' : Number(event.target.value))}>
      {image && <option value="fit">Fit width</option>}
      {[...new Set([0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, scale])].sort((a,b) => a-b).map(n =>
        <option value={n} key={n}>{Math.round(n * 100)}%</option>)}
    </select>
    <button type="button" className={css.tool} aria-label="Zoom in" disabled={scale >= 4}
      onClick={() => onChange(clampZoom(scale + 0.25))}>+</button>
  </div>
}
