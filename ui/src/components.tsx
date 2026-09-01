import { useEffect, useRef, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { api } from './api'
import { HOVER_CLOSE_MS, OVERLAP_PAGE } from './config'

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

    </div>
  )
}


/* ⓘ details popover — visible trigger (hover-only reveals are undiscoverable
   and unusable on touch), opens on hover, focus, or click; one card shows
   name/title/description/params/audiences without cluttering the table. */
export function ToolInfo({ payload, version, audiences }:
  { payload: any; version?: number; audiences?: string[] }) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<'external' | 'internal'>('external')
  const [copied, setCopied] = useState(false)
  const closeTimer = useRef<number>()
  const openNow = () => { window.clearTimeout(closeTimer.current); setOpen(true) }
  const closeSoon = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setOpen(false), HOVER_CLOSE_MS)
  }
  const props: [string, any][] = Object.entries(payload?.input_schema?.properties ?? {})
  const req = new Set<string>(payload?.input_schema?.required ?? [])
  const internalDesc = payload?.audiences?.internal?.overrides?.description
  const shownDesc = view === 'internal' ? (internalDesc ?? payload?.description) : payload?.description
  return (
    <span className="info-wrap" onMouseEnter={openNow} onMouseLeave={closeSoon}>
      <button className="info-btn" aria-label={`Details for ${payload?.name}`}
        onFocus={openNow} onBlur={closeSoon}>i</button>
      {open && (
        <div className="popcard" role="dialog" aria-label={`${payload?.name} details`}>
          <div className="popcard-head">
            <b className="score">{payload?.name}</b>
            {version != null && <span className="pill user">v{version}</span>}
            <button className="icon-act" style={{ marginLeft: 'auto' }}
              title="Copy the full tool definition (JSON, as published)"
              onClick={() => {
                navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
                setCopied(true); window.setTimeout(() => setCopied(false), 1500)
              }}>{copied ? '✓' : '⧉'}</button>
          </div>
          {payload?.title && <div className="popcard-title">{payload.title}</div>}
          <p className="popcard-desc">{shownDesc}
            {view === 'internal' && !internalDesc &&
              <span className="muted"> (inherits external)</span>}</p>
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
            <div style={{ marginTop: 8 }} title="Switch which audience's description is shown above">
              {audiences.map(a => (
                <button key={a} className={`pill-btn ${view === a ? 'on' : ''}`}
                  onClick={() => setView(a as any)}>
                  {a}{a === 'internal' && internalDesc ? ' •' : ''}</button>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </span>
  )
}

/* One overlap table for every page — same columns, same expandable breakdown,
   same cap. Rows expand on click ("Details" makes that discoverable). */
/* shared identity-avatar helpers — one hue per account, initials from name */
const AVATAR_HUES = [210, 275, 160, 25, 340, 190, 95, 0]
export const avatarHue = (s: string) =>
  `hsl(${AVATAR_HUES[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_HUES.length]} 45% 46%)`
export const avatarInitials = (name: string, email = '') =>
  (name || email).split(/[\s.@_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]!.toUpperCase()).join('')

/* cross-product handoff: one source of truth for the report text and the
   contact-the-admins action (Gmail compose prefilled — nothing auto-sends).
   Plain text on purpose — numbered blocks survive every mail client, real
   table columns do not. */
const handoffBody = (fix: any, pair: any) => {
  const cur = fix.current ?? {}
  const best = fix.packages[0]
  const P = (x: number) => `${Math.round(x * 100)}%`
  return [
    'Hi team,',
    '',
    'WHAT COLLIDES',
    `  ${pair.a.product_key}/${pair.a.name}  <->  ${pair.b.product_key}/${pair.b.name}`,
    `  similarity today: ${P(pair.score)} (flagged at >= ${P(fix.threshold ?? 0.5)})`,
    '  why it matters: at this similarity AI agents cannot reliably choose between',
    '  the two tools, so calls can land on the wrong product.',
    '',
    `SUGGESTED CHANGES — for ${fix.tool.product_key}/${fix.tool.name}`,
    '  These are recommendations, not requirements — feel free to change the',
    '  wording your own way. The tool editor re-checks similarity live as you',
    '  type, so you can verify your version before publishing.',
    '  1) name',
    `     current   : ${cur.name ?? fix.tool.name}`,
    `     suggested : ${best.name}`,
    '  2) title',
    `     current   : ${cur.title || '(none)'}`,
    `     suggested : ${best.title}`,
    '  3) description',
    `     current   : ${cur.description ?? '(none)'}`,
    `     suggested : ${best.description}`,
    '',
    `RESULT: with the suggested wording, similarity drops ${P(pair.score)} -> ${P(best.new_overall)}`,
    '— below the flagging threshold, verified against every product in the registry.',
    '',
    `Apply here: /p/${fix.tool.product_key}/tools/${fix.tool.id} (AI Registry)`,
    '',
    'Thanks!',
  ].join('\n')
}

const gmailCompose = (to: string[], cc: string[], subject: string, body: string) =>
  window.open('https://mail.google.com/mail/?view=cm&fs=1'
    + `&to=${encodeURIComponent(to.join(','))}`
    + (cc.length ? `&cc=${encodeURIComponent(cc.join(','))}` : '')
    + `&su=${encodeURIComponent(subject)}`
    + `&body=${encodeURIComponent(body)}`, '_blank')

const handoffSubject = (fix: any) =>
  `[AI Registry] Tool overlap: ${fix.tool.product_key}/${fix.tool.name} — suggested wording changes`
const handoffEmailTemplate = (fix: any, pair: any, to: string[], cc: string[]) =>
  `To: ${to.join(', ')}\n` + (cc.length ? `Cc: ${cc.join(', ')}\n` : '')
  + `Subject: ${handoffSubject(fix)}\n\n${handoffBody(fix, pair)}`

function HandoffActions({ fix, pair, canOpen, admins, supers }:
  { fix: any; pair: any; canOpen: boolean; admins: { email: string }[]; supers: { email: string }[] }) {
  if (canOpen) return (
    <RouterLink to={`/p/${fix.tool.product_key}/tools/${fix.tool.id}`}>
      <button className="primary small">Open {fix.tool.name} →</button>
    </RouterLink>)
  const to = admins.map(a => a.email)
  // super admins ride the CC line — minus anyone already a direct recipient
  const cc = supers.map(a => a.email).filter(e => !to.includes(e))
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      <button className="primary small"
        title={to.length
          ? `Opens a prefilled Gmail draft to ${to.join(', ')}${cc.length ? `, cc ${cc.join(', ')}` : ''} — review and send`
          : 'That product has no admins yet — the draft goes to the super admins'}
        onClick={() => gmailCompose(to.length ? to : cc, to.length ? cc : [],
          handoffSubject(fix), handoffBody(fix, pair))}>✉ Email the admins</button>
      <button className="icon-act" title="Copy the full email (to, cc, subject and body) instead"
        onClick={() => navigator.clipboard.writeText(handoffEmailTemplate(fix, pair, to.length ? to : cc, to.length ? cc : []))}>⧉</button>
    </span>)
}

/* on-scroll loading trigger: renders an invisible line; when it scrolls into
   view, onHit() fires (load the next page). The callback is kept in a ref so
   the observer never re-subscribes as parent state changes. */
export function Sentinel({ onHit }: { onHit: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const cb = useRef(onHit)
  cb.current = onHit
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ob = new IntersectionObserver(es => { if (es.some(x => x.isIntersecting)) cb.current() })
    ob.observe(el)
    return () => ob.disconnect()
  }, [])
  return <div ref={ref} style={{ height: 1 }} />
}

export function OverlapPairs({ report, explain, resolve, cap = OVERLAP_PAGE, showCross = true,
  labels = ['Tool A', 'Tool B'], productKey }: {
  report: { threshold: number; pairs: any[] } | null
  explain: (p: any) => Promise<any>
  resolve?: (p: any) => Promise<any>
  cap?: number
  showCross?: boolean
  labels?: [string, string]
  productKey?: string          // the product page this table sits on, if any —
}) {                           // banner wording is relative to the VIEWER
  const [open, setOpen] = useState('')
  const [visible, setVisible] = useState(cap)
  const [detail, setDetail] = useState<any>('idle')
  const [fix, setFix] = useState<any>('idle')
  const [fixOpen, setFixOpen] = useState<number | null>(null)
  const [otherRole, setOtherRole] = useState<string | null>(null)
  const [otherAdmins, setOtherAdmins] = useState<{ email: string; name: string }[]>([])
  const [otherSupers, setOtherSupers] = useState<{ email: string; name: string }[]>([])
  const pct = (x: number) => `${Math.round(x * 100)}%`
  const pairKey = (p: any) => [p.a.id, p.a.view ?? '', p.b.id, p.b.view ?? ''].join('|')
  const toggle = async (p: any) => {
    const k = pairKey(p)
    if (open === k) { setOpen(''); setDetail('idle'); setFix('idle'); return }
    setOpen(k); setDetail('loading'); setFix(resolve ? 'loading' : 'idle'); setFixOpen(null)
    setDetail(await explain(p).catch(() => 'error') ?? 'error')
    if (resolve) {
      const out = await resolve(p).catch(() => 'error') ?? 'error'
      setFix(out)
      if (out && out.side) {             // whichever product owns the fix: can
        api.product(out.tool.product_key) // THIS viewer act on it?
          .then((pr: any) => setOtherRole(pr.role)).catch(() => setOtherRole('none'))
        api.productAdmins(out.tool.product_key)   // …and who can, for the handoff email
          .then(r => { setOtherAdmins(r.admins); setOtherSupers(r.super_admins) })
          .catch(() => { setOtherAdmins([]); setOtherSupers([]) })
      }
    }
  }
  const pairs = report?.pairs ?? []
  useEffect(() => { setVisible(cap) }, [report, cap])
  const shown = pairs.slice(0, visible)
  return (
    <>
    <table className="overlap-table">
      <colgroup><col /><col />{showCross && <col style={{ width: 120 }} />}<col style={{ width: 92 }} /><col style={{ width: 90 }} /></colgroup>
      <thead><tr><th>{labels[0]}</th><th>{labels[1]}</th>{showCross && <th>Scope</th>}<th>Similarity</th><th /></tr></thead>
      <tbody>{shown.flatMap((p, i) => {
        const k = pairKey(p)
        const side = (s: any) => (
          <span className="tool-ref">
            <span className="tool-name" title={`${s.product_key}/${s.name}`}>
              <span className="muted">{s.product_key}/</span><b>{s.name}</b></span>
            {s.view && <span className={`view-tag ${s.view === 'internal' ? 'int' : ''}`}
              title={s.view === 'internal' ? "This side compares the tool's internal text"
                : "This side compares the tool's external text"}>
              {s.view === 'internal' ? 'internal' : 'external'}</span>}
          </span>)
        const rows = [(
          <tr key={i} style={{ cursor: 'pointer' }} onClick={() => toggle(p)}>
            <td>{side(p.a)}</td>
            <td>{side(p.b)}</td>
            {showCross && <td>{p.cross_product
              ? <span className="scope-tag" title="These tools live in different products">⇄ cross-product</span>
              : <span className="scope-tag" title="Both tools live in the same product">within product</span>}</td>}
            <td><span className={`sim-badge ${p.score >= 0.75 ? 'high' : ''}`}
              title={`internal retrieval score: ${p.cosine ?? p.score}`}>{pct(p.score)}</span></td>
            <td className="detail-cell">{open === k ? '▾ Hide' : '▸ Details'}</td>
          </tr>)]
        if (open === k) rows.push(
          <tr key={k + 'x'}><td colSpan={showCross ? 5 : 4} style={{ background: '#f8f9fa' }}>
            {detail === 'loading' ? <span className="muted">Analyzing…</span>
              : detail === 'error' ? <span className="muted">Couldn't load this comparison.</span>
              : <PairBreakdown ex={detail} />}
            {resolve && fix === 'loading' && (
              <p className="muted" style={{ margin: '8px 0 0' }}>
                <span className="rescore" style={{ margin: 0 }}><span className="spin" /> computing a verified resolution…</span></p>
            )}
            {resolve && fix && fix !== 'idle' && fix !== 'loading' && fix !== 'error' && (
              fix.side ? (
                <div style={{ marginTop: 8 }}>
                  {(() => {
                    const local = fix.tool.product_key === productKey
                    return (
                      <div className="handoff">
                        <span className={'dot ' + (local ? 'green' : 'red')} style={{ marginTop: 2 }} />
                        <span style={{ flex: 1 }}>
                          {local ? (
                            <><b>You can fix this here</b> — the verified fix is on this
                              product's <b className="score">{fix.tool.name}</b>.</>
                          ) : (
                            <><b>{productKey ? 'This fix belongs to another product:' : 'The verified fix is on:'}</b>{' '}
                              <span className="pill aud">{fix.tool.product_key}</span> — apply it on{' '}
                              <b className="score">{fix.tool.name}</b>.</>
                          )}
                        </span>
                        <HandoffActions fix={fix} pair={p} admins={otherAdmins} supers={otherSupers}
                          canOpen={otherRole === 'admin' || otherRole === 'super_admin'} />
                      </div>
                    )
                  })()}
                  {fix.packages.slice(0, 2).map((pk: any, i: number) => (
                    <div key={i}>
                      <div className="pkg-row" style={{ padding: '6px 0' }}>
                        <span className="pkg-num">{i + 1}</span>
                        <b className="score">{pk.name}</b>
                        <span className="muted">·</span>
                        <span className="pkg-title">{pk.title}</span>
                        <span className="muted">·</span>
                        <button className="small" onClick={() => setFixOpen(fixOpen === i ? null : i)}>
                          {fixOpen === i ? 'hide description' : 'description'}</button>
                        <span className="sugg-outcome" style={{ marginLeft: 'auto' }}>
                          <span className="delta ok">→ {Math.round(pk.new_overall * 100)}% ✓</span></span>
                        <button className="icon-act" title="Copy this fix (name, title and description)"
                          onClick={() => navigator.clipboard.writeText(
                            `${pk.name}\n${pk.title}\n${pk.description}`)}>⧉</button>
                      </div>
                      {fixOpen === i && <div className="resolve-desc">{pk.description}</div>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted" style={{ margin: '8px 0 0' }}>
                  No meaning-preserving rewrite separates this pair from either side — the two
                  descriptions assert the same thing. Consolidate, or state the genuine
                  difference{fix.template && <> (frame: <button className="icon-act"
                    title="Copy a description frame to fill in"
                    onClick={() => navigator.clipboard.writeText(fix.template)}>⧉</button>)</>}.
                </p>
              )
            )}
          </td></tr>)
        return rows
      })}
      {report === null && <tr><td colSpan={5} className="muted">Analyzing all pairs…</td></tr>}
      {report !== null && pairs.length === 0 && <tr><td colSpan={5} className="muted">
        No overlapping pairs at ≥ {pct(report.threshold)}. 🎉</td></tr>}
      </tbody>
    </table>
    {pairs.length > visible && <Sentinel onHit={() => setVisible(v => v + cap)} />}
    {pairs.length > cap && <p className="muted" style={{ marginTop: 8 }}>
      {Math.min(visible, pairs.length)} of {pairs.length} pairs loaded, highest similarity first{visible < pairs.length ? ' — scroll for more' : ''}.</p>}
    </>
  )
}


/** Map a pair-side view label to the audience whose override to explain:
 * a real audience key passes through, composites/base/all mean base text. */
export const viewAud = (v?: string) =>
  v && v !== 'all' && v !== 'base' && !v.includes(',') ? v : ''
