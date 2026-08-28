import { useEffect, useRef, useState } from 'react'

/** Two-step inline confirm: first click arms it ("Sure?"), second click within 3s
 * executes. No native dialogs — those are silently swallowed in embedded webviews. */
export function ConfirmButton({ label, confirmLabel = 'Sure?', title, onConfirm, icon = false }:
  { label: string; confirmLabel?: string; title?: string; onConfirm: () => void; icon?: boolean }) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<number>()
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const click = () => {
    if (!armed) {
      setArmed(true)
      timer.current = window.setTimeout(() => setArmed(false), 3000)
      return
    }
    window.clearTimeout(timer.current)
    setArmed(false)
    onConfirm()
  }
  if (icon) return (
    <button className="icon-btn" title={title ?? label} onClick={click}
      style={armed ? { background: '#fef2f2', color: 'var(--danger)', fontWeight: 700 } : undefined}>
      {armed ? confirmLabel : '✕'}
    </button>
  )
  return (
    <button className="small danger" title={title} onClick={click}
      style={armed ? { background: 'var(--danger)', color: '#fff', borderColor: 'var(--danger)' } : undefined}>
      {armed ? confirmLabel : label}
    </button>
  )
}


/** Shared similarity-breakdown renderer — numbers that visibly add up to the
 * headline, honest severity colors, and justified parameter scores. Used by
 * the overlap drawer and the editor's similar panel so they can never drift. */
export function PairBreakdown({ ex }: { ex: any }) {
  const pct = (v: number) => `${Math.round(v * 100)}%`
  const order = ['description', 'parameters', 'name', 'title']
  const fields = order.filter(f => f in (ex.subscores ?? {}))
  const sevClass = (s: string) => s === 'high' ? 'err' : s === 'medium' ? 'warn' : 'note'
  return (
    <div>
      {ex.contributions && (
        <table style={{ maxWidth: 460, marginBottom: 6 }}>
          <tbody>
            {fields.map(f => (
              <tr key={f}>
                <td style={{ border: 0, padding: '2px 8px 2px 0' }} className="muted">{f}</td>
                <td style={{ border: 0, padding: '2px 8px' }}><b className="score">{pct(ex.subscores[f])}</b></td>
                <td style={{ border: 0, padding: '2px 8px' }} className="muted">
                  → {pct(ex.contributions[f] ?? 0)} of total
                  {f === 'parameters' && (ex.shared?.parameters?.length ?? 0) === 0 && ex.params && (
                    <span> — nothing shared (this: {ex.params.a.join(', ') || 'none'} · other: {ex.params.b.join(', ') || 'none'})</span>
                  )}
                </td>
              </tr>
            ))}
            <tr><td style={{ border: 0, padding: '2px 8px 2px 0' }} className="muted">overall</td>
              <td style={{ border: 0, padding: '2px 8px' }}><b className="score">{pct(ex.overall ?? 0)}</b></td>
              <td style={{ border: 0, padding: '2px 8px' }} className="muted">weighted total</td></tr>
          </tbody>
        </table>
      )}
      {ex.shared?.terms?.length > 0 && <p className="muted" style={{ margin: '6px 0' }}>
        Common terms: {ex.shared.terms.slice(0, 8).map((t: string) =>
          <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}</p>}
      {ex.shared?.parameters?.length > 0 && <p className="muted" style={{ margin: '6px 0' }}>
        Common parameters: {ex.shared.parameters.map((t: string) =>
          <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}</p>}
      {(ex.recommendations ?? []).map((r: any, i: number) => (
        <div key={i} className={sevClass(r.severity)} style={{ margin: '6px 0' }}>{r.message}</div>))}
    </div>
  )
}
