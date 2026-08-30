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
  const fields = order.filter(f => f in (ex.contributions ?? {}))
  const sevClass = (s: string) => s === 'high' ? 'err' : s === 'medium' ? 'warn' : 'note'
  return (
    <div>
      {ex.contributions && (
        <p style={{ margin: '2px 0 6px' }}>
          {fields.map(f => (
            <span key={f} className="param-op" style={{ marginRight: 6 }}
              title={`${f}: ${pct(ex.subscores?.[f] ?? 0)} similar${f === 'parameters' && ex.params
                ? ` (this: ${ex.params.a.join(', ') || 'none'} · other: ${ex.params.b.join(', ') || 'none'})` : ''}`}>
              {f} {pct(ex.contributions[f] ?? 0)}
            </span>
          ))}
          <b className="score">= {pct(ex.overall ?? 0)}</b>
        </p>
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


/* ⓘ details popover — visible trigger (hover-only reveals are undiscoverable
   and unusable on touch), opens on hover, focus, or click; one card shows
   name/title/description/params/audiences without cluttering the table. */
export function ToolInfo({ payload, version, audiences }:
  { payload: any; version?: number; audiences?: string[] }) {
  const [open, setOpen] = useState(false)
  const props: [string, any][] = Object.entries(payload?.input_schema?.properties ?? {})
  const req = new Set<string>(payload?.input_schema?.required ?? [])
  return (
    <span className="info-wrap" onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}>
      <button className="info-btn" aria-label={`Details for ${payload?.name}`}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>i</button>
      {open && (
        <div className="popcard" role="dialog" aria-label={`${payload?.name} details`}>
          <div className="popcard-head">
            <b className="score">{payload?.name}</b>
            {version != null && <span className="pill user">v{version}</span>}
          </div>
          {payload?.title && <div className="popcard-title">{payload.title}</div>}
          <p className="popcard-desc">{payload?.description}</p>
          {props.length > 0 && (
            <table className="popcard-table">
              <thead><tr><th>Param</th><th>Type</th><th>Req</th><th>Description</th></tr></thead>
              <tbody>{props.map(([n, sc]) => (
                <tr key={n}>
                  <td className="score">{n}</td>
                  <td>{sc?.type ?? '—'}</td>
                  <td>{req.has(n) ? '✓' : ''}</td>
                  <td className="muted">{sc?.description ?? ''}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {audiences?.length ? (
            <div style={{ marginTop: 8 }}>{audiences.map(a =>
              <span key={a} className="pill aud" style={{ marginRight: 4 }}>{a}</span>)}</div>
          ) : null}
        </div>
      )}
    </span>
  )
}

/* One overlap table for every page — same columns, same expandable breakdown,
   same cap. Rows expand on click ("Details" makes that discoverable). */
export function OverlapPairs({ report, explain, cap = 50, showCross = true,
  labels = ['Tool A', 'Tool B'] }: {
  report: { threshold: number; pairs: any[] } | null
  explain: (p: any) => Promise<any>
  cap?: number
  showCross?: boolean
  labels?: [string, string]
}) {
  const [open, setOpen] = useState('')
  const [detail, setDetail] = useState<any>('idle')
  const pct = (x: number) => `${Math.round(x * 100)}%`
  const toggle = async (p: any) => {
    const k = p.a.id + p.b.id
    if (open === k) { setOpen(''); setDetail('idle'); return }
    setOpen(k); setDetail('loading')
    setDetail(await explain(p).catch(() => 'error') ?? 'error')
  }
  const pairs = report?.pairs ?? []
  const shown = pairs.slice(0, cap)
  return (
    <>
    <table>
      <thead><tr><th>{labels[0]}</th><th>{labels[1]}</th><th>Similarity</th>{showCross && <th>Cross-product</th>}<th /></tr></thead>
      <tbody>{shown.flatMap((p, i) => {
        const k = p.a.id + p.b.id
        const rows = [(
          <tr key={i} style={{ cursor: 'pointer' }} onClick={() => toggle(p)}>
            <td><b className="score">{p.a.product_key}/{p.a.name}</b>
              {p.a.view && <span className={`pill ${p.a.view === 'internal' ? 'aud' : 'user'}`}
                style={{ marginLeft: 6 }}
                title={p.a.view === 'internal' ? "This side compares the tool's internal text"
                  : "This side compares the tool's external text"}>
                {p.a.view === 'internal' ? 'internal' : 'external'}</span>}</td>
            <td><b className="score">{p.b.product_key}/{p.b.name}</b>
              {p.b.view && <span className={`pill ${p.b.view === 'internal' ? 'aud' : 'user'}`}
                style={{ marginLeft: 6 }}
                title={p.b.view === 'internal' ? "This side compares the tool's internal text"
                  : "This side compares the tool's external text"}>
                {p.b.view === 'internal' ? 'internal' : 'external'}</span>}</td>
            <td><b className="score" title={`internal retrieval score: ${p.cosine ?? p.score}`}>{pct(p.score)}</b></td>
            {showCross && <td>{p.cross_product ? <span className="pill on">yes</span> : <span className="pill user">no</span>}</td>}
            <td className="detail-cell">{open === k ? '▾ Hide' : '▸ Details'}</td>
          </tr>)]
        if (open === k) rows.push(
          <tr key={k + 'x'}><td colSpan={5} style={{ background: '#f8f9fa' }}>
            {detail === 'loading' ? <span className="muted">Analyzing…</span>
              : detail === 'error' ? <span className="muted">Couldn't load this comparison.</span>
              : <PairBreakdown ex={detail} />}
          </td></tr>)
        return rows
      })}
      {report === null && <tr><td colSpan={5} className="muted">Analyzing all pairs…</td></tr>}
      {report !== null && pairs.length === 0 && <tr><td colSpan={5} className="muted">
        No overlapping pairs at ≥ {pct(report.threshold)}. 🎉</td></tr>}
      </tbody>
    </table>
    {pairs.length > cap && <p className="muted" style={{ marginTop: 8 }}>
      Showing the top {cap} of {pairs.length} pairs, highest similarity first.</p>}
    </>
  )
}


/** Map a pair-side view label to the audience whose override to explain:
 * a real audience key passes through, composites/base/all mean base text. */
export const viewAud = (v?: string) =>
  v && v !== 'all' && v !== 'base' && !v.includes(',') ? v : ''
