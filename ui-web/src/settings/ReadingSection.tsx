import { useStore } from '@nanostores/react'
import { $reading, DEFAULT_READING, setReading, WORK_DETAIL_MODES } from '../state/preferences.ts'
import css from './Settings.module.css'

const DETAILS = {
  compact: 'Group work with minimal summaries.',
  standard: 'Group work and show the current command or thought.',
  detailed: 'Show individual steps while working; group completed work.',
  verbose: 'Keep every step visible.',
}

export function ReadingSection() {
  const p = useStore($reading)
  return <>
    <div className={css.row}>
      <div className={css.rowHead}><span className={css.rowName}>Work details</span></div>
      <div className={css.rowMeta}>How tool calls and thoughts appear in conversations.</div>
      <div className={css.choiceRow}>
        {WORK_DETAIL_MODES.map(mode => <button key={mode} type="button" aria-pressed={p.workDetails === mode}
          className={[css.choice, p.workDetails === mode ? css.choiceOn : ''].join(' ')}
          title={DETAILS[mode]} onClick={() => setReading({ workDetails: mode })}>
          {mode[0]?.toUpperCase()}{mode.slice(1)}
        </button>)}
      </div>
      <div className={css.rowMeta}>{DETAILS[p.workDetails]}</div>
      <label className={css.readingLabel}>Collapse completed work
        <select aria-label="Collapse completed work" value={p.collapseTiming}
          onChange={event => setReading({ collapseTiming: event.target.value === 'next-input' ? 'next-input' : 'completion' })}>
          <option value="completion">When the turn completes</option>
          <option value="next-input">When I send the next message</option>
        </select>
      </label>
    </div>
    <div className={css.row}>
      <div className={css.rowHead}><span className={css.rowName}>Fonts and sizes</span></div>
      <div className={css.rowMeta}>Saved in this browser. Font families must be installed on your computer.</div>
      {(['text', 'code', 'terminal'] as const).map(role => <div className={css.fontRow} key={role}>
        <label className={css.readingLabel}>{role === 'text' ? 'Conversation' : role === 'code' ? 'Code' : 'Terminal output'}
          <input aria-label={`${role} font`} className={css.keyInput} placeholder="System default"
            value={p[`${role}Font`]} onChange={event => setReading({ [`${role}Font`]: event.target.value })} />
        </label>
        <label className={css.readingLabel}>Size
          <input type="number" aria-label={`${role} font size`} min={role === 'text' ? 12 : 10}
            max={role === 'code' ? 20 : 24} value={p[`${role}Size`]}
            onChange={event => { if (event.target.value !== '') setReading({ [`${role}Size`]: event.target.valueAsNumber }) }} />
        </label>
      </div>)}
      <button type="button" className={css.choice} onClick={() => setReading({
        textSize: DEFAULT_READING.textSize, codeSize: DEFAULT_READING.codeSize,
        terminalSize: DEFAULT_READING.terminalSize, textFont: '', codeFont: '', terminalFont: '',
      })}>Reset fonts</button>
    </div>
  </>
}
