import { useEffect, useRef, useState } from 'react'
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, Similar, ValidationErr } from '../api'
import { toast } from '../App'
import SchemaTree from '../SchemaTree'
import { OverlapPairs, viewAud } from '../components'

type Overlay = { enabled?: boolean; overrides?: any }
type Matches = { matches: Similar[]; top_explain: any; threshold?: number; flagged_audience?: string | null
  suggestions?: { names: { name: string; title: string; new_overall: number | null }[]; titles: { title: string; new_overall: number | null }[]; descriptions: { text: string; new_overall: number | null; keeps_content?: boolean }[]; packages?: { name: string; title: string; description: string; new_overall: number }[]; bundle?: { name: string; title: string; description: string; new_overall: number } | null; template?: string | null; current_name_match?: number; description_tip?: string | null; resolution_hint?: string | null } | null }

const emptyPayload = () => ({
  name: '', description: '',
  input_schema: { type: 'object', properties: {} } as any,
  audiences: {} as Record<string, Overlay>,
})

const MCP_HINTS = [
  { key: 'readOnlyHint', label: 'Read-only', help: 'Does not modify any state' },
  { key: 'destructiveHint', label: 'Destructive', help: 'May delete or irreversibly change data' },
  { key: 'idempotentHint', label: 'Idempotent', help: 'Safe to call repeatedly with the same args' },
  { key: 'openWorldHint', label: 'Open world', help: 'Interacts with external systems / the internet' },
]

export default function ToolEditor() {
  const { productKey = '', entityId } = useParams()
  const nav = useNavigate()
  const isNew = !entityId
  const [payload, setPayload] = useState<any>(emptyPayload())
  const [audiences, setAudiences] = useState<string[]>(['external'])
  const [tab, setTab] = useState<string>('base')
  const [errors, setErrors] = useState<ValidationErr[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [versions, setVersions] = useState<any[]>([])
  const [saved, setSaved] = useState('')
  const [version, setVersion] = useState(0)
  const [canEdit, setCanEdit] = useState(true)
  const [matches, setMatches] = useState<Matches | null>(null)
  const [checking, setChecking] = useState(false)
  const [savedPayload, setSavedPayload] = useState<any>(null)
  const [savedReport, setSavedReport] = useState<{ threshold: number; pairs: any[] } | null>(null)
  const [staleDraft, setStaleDraft] = useState<{ from: number; to: number } | null>(null)
  const debounce = useRef<number>()
  const matchDebounce = useRef<number>()
  const draftKey = `draft:${productKey}:${entityId ?? 'new'}`

  useEffect(() => {
    api.product(productKey).then(p => setCanEdit(p.role === 'admin' || p.role === 'super_admin'))
    api.audiences(productKey).then(a => setAudiences(a.map(x => x.key)))
    if (entityId) {
      api.entity(productKey, entityId).then(e => {
        setSavedPayload(e.payload); setVersion(e.version)
        // a stored draft survives navigation until published or discarded;
        // it remembers which published version it was based on, so a publish
        // by someone else while the draft existed is surfaced, not silently
        // overwritten
        const raw = localStorage.getItem(draftKey)
        const sp = raw ? JSON.parse(raw) : null
        const draftPayload = sp?.payload ?? sp
        setPayload(draftPayload ?? e.payload)
        if (sp?.v && sp.v !== e.version) setStaleDraft({ from: sp.v, to: e.version })
        api.duplicates(productKey, 'all').then(r => setSavedReport(
          { ...r, pairs: r.pairs
              .filter((p: any) => p.a.id === entityId || p.b.id === entityId)
              .map((p: any) => p.a.id === entityId ? p : { ...p, a: p.b, b: p.a }) }))
          .catch(() => {})                                    // same scan as the overlap pages
      })
      api.versions(productKey, entityId).then(setVersions)
    } else {
      const raw = localStorage.getItem(draftKey)
      if (raw) { const sp = JSON.parse(raw); setPayload(sp?.payload ?? sp) }
    }
  }, [productKey, entityId])

  // the draft lives in localStorage until published or explicitly discarded;
  // when the draft equals the published payload there is no draft to keep
  useEffect(() => {
    if (isNew) {
      if (payload.name || payload.description)
        localStorage.setItem(draftKey, JSON.stringify(payload))
      return
    }
    if (!savedPayload) return
    if (JSON.stringify(payload) !== JSON.stringify(savedPayload))
      localStorage.setItem(draftKey, JSON.stringify({ v: version, payload }))
    else localStorage.removeItem(draftKey)
  }, [payload, draftKey, savedPayload, isNew, version])

  // live preview — the same validate+resolve pipeline as Save
  useEffect(() => {
    window.clearTimeout(debounce.current)
    if (!payload.name) return
    debounce.current = window.setTimeout(async () => {
      try {
        const r = await api.dryRun(productKey, { type: 'tool', payload })
        setErrors(r.errors); setPreview(r.valid ? r.resolved : null)
      } catch { /* keep last preview */ }
    }, 350)
  }, [payload, productKey])

  // live similarity, two tiers so typing scales:
  //  1. CHEAP check (matches + % only) after the user finishes a word —
  //     600ms on a word/sentence boundary, 1800ms mid-word
  //  2. the expensive resolution packages are fetched separately (below),
  //     only when flagged and only once the draft has settled
  const prevTexts = useRef<string[]>([])
  const lastCheckSig = useRef('')
  useEffect(() => {
    window.clearTimeout(matchDebounce.current)
    if (!payload.name && !payload.description) return
    // whitespace-only edits are noise: normalize everything similarity reads
    // and skip entirely when the normalized content is unchanged
    const norm = (s: any) => String(s ?? '').replace(/\s+/g, ' ').trim()
    const sig = JSON.stringify([
      norm(payload.name), norm(payload.title), norm(payload.description),
      JSON.stringify(payload.input_schema),
      Object.keys(payload.audiences ?? {}).sort().map(k => {
        const o = (payload.audiences?.[k] ?? {}) as any
        return [k, norm(o.overrides?.description), norm(o.overrides?.title), o.enabled !== false]
      }),
      tab === 'base' ? 'base' : audiences.includes(tab) ? tab : 'base',
    ])
    if (sig === lastCheckSig.current) return
    // find the text the user is ACTUALLY editing (base or any audience
    // override) so mid-word typing gets the long delay on every tab
    const texts = [String(payload.description ?? ''), String(payload.title ?? ''),
      ...Object.keys(payload.audiences ?? {}).sort().flatMap(k => {
        const o = (payload.audiences?.[k]?.overrides ?? {}) as any
        return [String(o.description ?? ''), String(o.title ?? '')]
      })]
    const changed = texts.find((x, i) => prevTexts.current.length > 0
      && x !== prevTexts.current[i]) ?? String(payload.description ?? '')
    prevTexts.current = texts
    const tail = changed.slice(-1)
    const boundary = tail === '' || /[\s.,;:!?)]/.test(tail)
    const view = tab === 'base' ? 'base'
      : audiences.includes(tab) ? tab : 'base'
    matchDebounce.current = window.setTimeout(() => {
      lastCheckSig.current = sig
      setChecking(true)
      api.similarPreview(productKey, { type: 'tool', suggestions: false, view,
        payload: { ...payload, _entity_id: entityId } })
        .then(r => setMatches(prev => ({ ...r,
          // keep already-loaded packages while the % updates
          suggestions: r.matches[0] && prev?.suggestions
            && r.matches[0].id === prev.matches[0]?.id ? prev.suggestions : r.suggestions })))
        .catch(() => {}).finally(() => setChecking(false))
    }, boundary ? 600 : 1800)
  }, [payload.name, payload.title, payload.description, payload.input_schema,
      JSON.stringify(payload.audiences ?? {}), productKey, tab])

  // tier 2: fetch resolutions only when flagged, after the draft settles
  useEffect(() => {
    const top = matches?.matches?.[0]
    const th2 = matches?.threshold ?? 0.5
    if (!top || top.score < th2 || matches?.suggestions) return
    const view = tab === 'base' ? 'base'
      : audiences.includes(tab) ? tab : 'base'
    const id = window.setTimeout(() => {
      api.similarPreview(productKey, { type: 'tool', suggestions: true, view,
        payload: { ...payload, _entity_id: entityId } })
        .then(setMatches).catch(() => {})
    }, 900)
    return () => window.clearTimeout(id)
  }, [matches, productKey, tab])


  const BASE_KEYS = ['name', 'title', 'description', 'input_schema', 'annotations']
  const baseDirty = !isNew && !!savedPayload && BASE_KEYS.some(k =>
    JSON.stringify((payload as any)[k] ?? null) !== JSON.stringify((savedPayload as any)[k] ?? null))
  const audDirty = (a: string) => !isNew && !!savedPayload &&
    JSON.stringify(payload.audiences?.[a] ?? null) !== JSON.stringify(savedPayload.audiences?.[a] ?? null)
  const discardSection = (section: string) => {
    if (!savedPayload) return
    if (section === 'base') {
      setPayload((p: any) => {
        const next = { ...p }
        for (const k of BASE_KEYS) next[k] = structuredClone((savedPayload as any)[k])
        return next
      })
    } else {
      setPayload((p: any) => {
        const a = { ...(p.audiences ?? {}) }
        const pub = savedPayload.audiences?.[section]
        if (pub) a[section] = structuredClone(pub); else delete a[section]
        return { ...p, audiences: a }
      })
    }
    toast(`${section === 'base' ? 'external' : section} draft discarded — showing the published version`)
  }

  // the durable "before": the published collision for the current view —
  // survives tab switches because it is derived, not remembered in state
  const publishedPrevFor = (viewTab: string) => {
    const th0 = savedReport?.threshold ?? 0.5
    const mine = (savedReport?.pairs ?? []).filter((p: any) => viewTab === 'internal'
      ? p.a.view === 'internal' : p.a.view !== 'internal')
    const top0 = mine.reduce((m: any, p: any) => !m || p.score > m.score ? p : m, null)
    return top0 && top0.score >= th0
      ? { score: top0.score, name: top0.b.name, product_key: top0.b.product_key } : null
  }

  const set = (patch: any) => setPayload((p: any) => ({ ...p, ...patch }))
  const setSchema = (fn: (s: any) => any) => setPayload((p: any) => ({ ...p, input_schema: fn(structuredClone(p.input_schema)) }))
  const setOverlay = (aud: string, fn: (o: Overlay) => Overlay) =>
    setPayload((p: any) => ({ ...p, audiences: { ...p.audiences, [aud]: fn(structuredClone(p.audiences?.[aud] ?? {})) } }))

  const save = async () => {
    setErrors([]); setSaved('')
    try {
      if (isNew) {
        const e = await api.createEntity(productKey, { type: 'tool', payload })
        localStorage.removeItem(draftKey)
        nav(`/p/${productKey}/tools/${e.id}`)
      } else {
        const e = await api.updateEntity(productKey, entityId!, { payload })
        localStorage.removeItem(draftKey); setSavedPayload(payload); setStaleDraft(null)
        api.duplicates(productKey, 'all').then(r => setSavedReport(
          { ...r, pairs: r.pairs
              .filter((p: any) => p.a.id === entityId || p.b.id === entityId)
              .map((p: any) => p.a.id === entityId ? p : { ...p, a: p.b, b: p.a }) }))
          .catch(() => {})                                    // published -> tab updates now
        setVersion(e.version); setSaved(`Saved as v${e.version} — SDKs updated live.`)
        toast(`Saved v${e.version} — live everywhere`)
        api.versions(productKey, entityId!).then(setVersions)
      }
    } catch (ex) {
      if (ex instanceof ApiError && ex.validationErrors.length) setErrors(ex.validationErrors)
      else setErrors([{ path: '', code: 'error', message: String((ex as any)?.detail ?? 'Save failed') }])
    }
  }

  return (
    <>
      <div className="topbar">
        <div><h1>{isNew ? 'New tool' : payload.name} {!isNew && <span className="muted">v{version}</span>}</h1>
          <span className="muted">Edits publish to every running MCP server the moment you save.</span></div>
        <div className="row">
          {!isNew && savedPayload && JSON.stringify(payload) !== JSON.stringify(savedPayload) && (<>
            <span className="pill off" title="Your edits are stored as a draft — they survive navigation but are not served until you Save & publish">
              draft — not published</span>
            <button className="small" title="Throw away the whole draft and go back to the published version"
              onClick={() => { localStorage.removeItem(draftKey); setPayload(savedPayload)
                setStaleDraft(null); toast('Draft discarded — showing the published version') }}>Discard draft</button>
          </>)}
          <button onClick={() => nav(`/p/${productKey}`)}>Back</button>
          {canEdit
            ? <button className="primary" onClick={save}
                disabled={errors.length > 0 || !payload.name || !payload.description?.trim()}
                title={!payload.name || !payload.description?.trim()
                  ? 'Name and description are required' : undefined}>
                {isNew ? 'Create tool' : 'Save & publish'}</button>
            : <span className="pill user">read-only</span>}
        </div>
      </div>
      {saved && <div className="ok-banner">{saved}</div>}
      {staleDraft && (
        <div className="err">⚠ The published version changed (v{staleDraft.from} → v{staleDraft.to})
          while this draft existed — someone else edited this tool. Review your ✎ changes before
          publishing (your draft would replace theirs), or discard the draft to take their version.</div>
      )}
      <div className="tabs">
        <button className={tab === 'base' ? 'active' : ''} onClick={() => setTab('base')}
          title="The definition every caller gets — external is the base">external
          {baseDirty && <span className="draft-mark" title="unsaved draft changes on this tab">✎</span>}</button>
        <button className={tab === 'internal' ? 'active' : ''} onClick={() => setTab('internal')}
          title={!audiences.includes('internal') ? 'Internal audience is not enabled for this product yet'
            : payload.audiences?.internal && Object.keys(payload.audiences.internal).length
              ? 'internal has its own wording on this tool (• = overridden)'
              : 'internal inherits the external wording on this tool'}>
          internal{audiences.includes('internal') && payload.audiences?.internal && Object.keys(payload.audiences.internal).length ? ' •' : ''}
          {audiences.includes('internal') && audDirty('internal') && <span className="draft-mark" title="unsaved draft changes on this tab">✎</span>}
        </button>
        {!isNew && (
          <button className={tab === 'similar' ? 'active' : ''} onClick={() => setTab('similar')}>
            Similar tools
            {savedReport && (savedReport.pairs.length
              ? <span className="dot red" title="Overlaps at or above the threshold" />
              : <span className="dot green" title="No overlaps" />)}
          </button>
        )}
        {!isNew && (
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
            History{versions.length ? ` (${versions.length})` : ''}</button>
        )}
      </div>
      {tab === 'base' ? (
        <BaseForm payload={payload} set={set} setSchema={setSchema} isNew={isNew} setOverlay={setOverlay}
          savedPayload={savedPayload} discardSection={discardSection} prevTop={publishedPrevFor('base')}
          matches={matches} setPayload={setPayload} checking={checking}
          dirty={!isNew && savedPayload !== null &&
                 JSON.stringify(payload) !== JSON.stringify(savedPayload)}
          preview={preview} previewTab="base" errors={errors} />
      ) : tab === 'internal' && !audiences.includes('internal') ? (
        <div className="card">
          <h2>Internal audience is not enabled for this product</h2>
          <p className="muted">Enable it to give internal callers their own wording for this
            product's tools (they inherit the external text until overridden).</p>
          {canEdit && <button className="primary" onClick={async () => {
            try {
              await api.addAudience(productKey, { key: 'internal' })
              setAudiences(a => [...a, 'internal']); toast('Internal audience enabled')
            } catch (ex: any) { toast(String(ex.detail ?? 'Failed')) }
          }}>Enable internal audience</button>}
        </div>
      ) : tab !== 'similar' && tab !== 'history' ? (
        <AudienceForm aud={tab} overlay={payload.audiences?.[tab] ?? {}}
          basePayload={payload} setOverlay={setOverlay} setTab={setTab}
          savedPayload={savedPayload} discardSection={discardSection} prevTop={publishedPrevFor(tab)}
          errors={errors} matches={matches} checking={checking} set={set} />
      ) : null}

      {tab === 'similar' && !isNew && (
        <div className="card">
          <h2>{savedReport && (savedReport.pairs.length
              ? <span className="dot red" /> : <span className="dot green" />)}
            Similar tools <span className="muted">(as published — one row per audience view, same
            numbers as the overlap pages; the warning on Base tracks your draft live)</span></h2>
          <OverlapPairs report={savedReport} showCross={false}
            labels={['This tool', 'Overlaps with']}
            explain={p => api.explainPair(productKey, p.a.id, p.b.id,
              viewAud(p.a.view), viewAud(p.b.view))} />
        </div>
      )}
      {tab === 'history' && !isNew && (
        <div className="card">
          <h2>Version history</h2>
          <table><tbody>{versions.map(v => (
                <tr key={v.version}><td className="score">v{v.version}</td>
                  <td className="muted">{v.note || v.changes || '—'}</td>
                  <td className="muted">{new Date(v.created_at).toLocaleString()}</td>
                  <td>{v.version === version
                    ? <span className="pill on">active</span>
                    : canEdit && <button className="small" onClick={async () => {
                        const e = await api.rollback(productKey, entityId!, v.version)
                        setPayload(e.payload); setVersion(e.version)
                        api.versions(productKey, entityId!).then(setVersions)
                        setSaved(`v${v.version} is now active (published as v${e.version}) — SDKs updated live.`)
                        toast(`v${v.version} is active again`)
                      }}>Make active</button>}</td></tr>
              ))}</tbody></table>
        </div>
      )}
    </>
  )
}

/* live preview panel — rendered on demand next to the editor controls */
function PreviewPanel({ preview, tab, errors }: { preview: any; tab: string; errors: any[] }) {
  return (
    <div style={{ marginTop: 10 }}>
      <p className="muted" style={{ margin: '0 0 6px' }}>What each audience sees — rendered by the
        same validate+resolve pipeline as Save, so it cannot lie.</p>
      <div className="preview">{preview
        ? JSON.stringify(tab === 'base' ? preview : { [tab]: preview[tab] }, null, 2)
        : errors.length ? '⚠ fix the errors to see the preview' : '…'}</div>
    </div>
  )
}

function draftRows(section: string, draft: any, published: any): [string, string, string][] {
  const rows: [string, string, string][] = []
  if (!published) return rows
  if (section === 'base') {
    for (const k of ['name', 'title', 'description'])
      if ((draft[k] ?? '') !== (published[k] ?? ''))
        rows.push([k, String(published[k] ?? '—'), String(draft[k] ?? '—')])
    if (JSON.stringify(draft.input_schema) !== JSON.stringify(published.input_schema))
      rows.push(['parameters', 'published schema', 'edited schema'])
    if (JSON.stringify(draft.annotations ?? null) !== JSON.stringify(published.annotations ?? null))
      rows.push(['annotations', 'published hints', 'edited hints'])
  } else {
    const d = draft.audiences?.[section]?.overrides?.description
    const p = published.audiences?.[section]?.overrides?.description
    if ((d ?? '') !== (p ?? ''))
      rows.push(['description', p ?? '(inheriting external)', d ?? '(inheriting external)'])
    const de = draft.audiences?.[section]?.enabled !== false
    const pe = published.audiences?.[section]?.enabled !== false
    if (de !== pe) rows.push(['visibility', pe ? 'enabled' : 'hidden', de ? 'enabled' : 'hidden'])
  }
  return rows
}

function SectionDraftBanner({ section, draft, published, discard }:
  { section: string; draft: any; published: any; discard: (s: string) => void }) {
  const [open, setOpen] = useState(false)
  const rows = draftRows(section, draft, published)
  if (!rows.length) return null
  return (
    <div className="draft-banner" style={{ display: 'block' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span className="draft-mark">✎</span> This tab has unsaved draft changes — not published.
        <button className="small" style={{ marginLeft: 8 }}
          onClick={() => setOpen(v => !v)}>{open ? 'Hide changes' : 'View changes'}</button>
        <button className="small" style={{ marginLeft: 4 }}
          title="Restore only this tab to its published version"
          onClick={() => discard(section)}>Discard this tab's draft</button>
      </div>
      {open && (
        <table className="resolve-table" style={{ marginTop: 8 }}><tbody>
          {rows.map(([field, pub, dr]) => (
            <tr key={field}><th>{field}</th>
              <td className="cur" title={pub}>{pub}</td>
              <td className="arr">→</td>
              <td className="cur" style={{ color: '#7a4f01' }} title={dr}>{dr}</td></tr>
          ))}
        </tbody></table>
      )}
    </div>
  )
}

function SimilarityWarning({ matches, checking, payload, set, setOverlay, dirty, aud, prevTop }: any) {
  const drv = (matches as any)?.flagged_audience ?? null   // null = external/base text drives
  const here = aud ?? null
  const owns = drv === here
  // a fix must land on the TEXT that collides: base description normally, or
  // the driving audience's override when the warning says an override drives
  const applyDesc = (desc: string) => {
    const drv = (matches as any)?.flagged_audience
    if (drv && setOverlay) {
      setOverlay(drv, (o: any) => ({ ...o, overrides: { ...(o.overrides ?? {}), description: desc } }))
      return drv
    }
    set({ description: desc })
    return null
  }
  // remember the collision we were flagged at, so the success banner can
  // show the actual reduction (70% -> 25%), not just the new number
  const [lastFlagged, setLastFlagged] = useState<any>(null)
  const [showResDesc, setShowResDesc] = useState<number | null>(null)
  const th = matches?.threshold ?? 0.5
  const top = matches?.matches?.[0]
  const flagged = top && top.score >= th
  useEffect(() => {
    if (flagged) setLastFlagged({ score: top.score, name: top.name, product_key: top.product_key })
  }, [flagged, top?.score, top?.name])
  // every state renders something: checking, clean, fixed, or flagged —
  return (
    <>
      {checking && !flagged && (
        <p className="muted" style={{ margin: '6px 0 0' }}>
          <span className="rescore" style={{ margin: 0 }}><span className="spin" /> checking similarity…</span>
        </p>
      )}
      {!checking && !flagged && top && (() => {
        const prev = lastFlagged ?? prevTop
        return (
          <p className="muted" style={{ margin: '6px 0 0' }}>
            <span className="dot green" />
            {prev && prev.score >= th ? (<>
              Looks distinct now —{' '}
              {prev.name === top.name
                ? <><s>{Math.round(prev.score * 100)}%</s> → <b>{Math.round(top.score * 100)}%</b>{' '}
                    with <b className="score">{top.product_key}/{top.name}</b></>
                : <>was <s>{Math.round(prev.score * 100)}%</s> with{' '}
                    <b className="score">{prev.product_key}/{prev.name}</b>; closest is now{' '}
                    <b className="score">{top.product_key}/{top.name}</b> at{' '}
                    <b>{Math.round(top.score * 100)}%</b></>}, below your{' '}
              {Math.round(th * 100)}% threshold.
            </>) : (<>
              No conflicts — closest match is <b className="score">{top.product_key}/{top.name}</b>{' '}
              at {Math.round(top.score * 100)}% (threshold {Math.round(th * 100)}%).
            </>)}
          </p>
        )
      })()}
      {flagged && (
        <div className="err" style={{ marginTop: 6 }}>
          ⚠ <b className="score">{Math.round(top.score * 100)}%</b> match with{' '}
          <b className="score">{top.product_key}/{top.name}</b>
          <span className="muted"> (threshold {Math.round(th * 100)}%{dirty ? ' · based on your unsaved edits — the overlap page shows the saved version' : ''})</span>
          {(matches as any).flagged_audience && <span className="muted"> · driven by your <b>{(matches as any).flagged_audience}</b> override text</span>}
          {(top as any).match_view && (top as any).match_view !== 'base' && <span className="muted"> · vs their <b>{(top as any).match_view}</b> text</span>}
          {checking && <span className="muted"> · rechecking…</span>}
          {(top as any).breakdown?.contributions && (
            <span style={{ marginLeft: 10, fontWeight: 400 }}>
              {['description', 'parameters', 'name', 'title']
                .filter(f => f in (top as any).breakdown.contributions)
                .map(f => (
                  <span key={f} className="param-op" style={{ marginRight: 5 }}
                    title={`${f}: ${Math.round(((top as any).breakdown.subscores?.[f] ?? 0) * 100)}% similar`}>
                    {f} {Math.round(((top as any).breakdown.contributions[f] ?? 0) * 100)}%
                  </span>
                ))}
            </span>
          )}
          {owns && !matches.suggestions && (
            <div className="sugg-row" style={{ marginTop: 8 }}>
              <span className="rescore"><span className="spin" /> finding verified resolutions…</span>
            </div>
          )}
          {owns && matches.suggestions && !matches.suggestions.packages?.length && (
            <div className="resolve-card dup" style={{ marginTop: 8 }}>
              <div className="resolve-head" style={{ color: '#3c4043' }}>
                Every rewrite we tested still matches {top.product_key}/{top.name} — the two
                descriptions mean the same thing
              </div>
              <p style={{ margin: '8px 0 4px', fontWeight: 400 }}>
                Automatic fixes only reword; they never change meaning. To resolve this you need
                a fact only you know — pick one:
              </p>
              <p style={{ margin: '4px 0', fontWeight: 400 }}>
                <b>1.</b> They really are the same → keep <b className="score">{top.product_key}/{top.name}</b>{' '}
                and remove this tool (or vice versa).
              </p>
              <p style={{ margin: '4px 0', fontWeight: 400 }}>
                <b>2.</b> The conflict may be fixable from the other side — its wording may have
                drifted into this tool's domain:{' '}
                <RouterLink to={`/p/${top.product_key}/tools/${top.id}`}>
                  open {top.product_key}/{top.name}</RouterLink>{' '}
                and check its warning for verified rewrites of this pair.
              </p>
              <p style={{ margin: '4px 0', fontWeight: 400 }}>
                <b>3.</b> They differ → say how, in this description. Fill this frame with your
                data source, when to use which, and what this tool never does:
              </p>
              {matches.suggestions.template && (
                <div className="template-text">
                  <code>{matches.suggestions.template}</code>
                  <button className="icon-act" title="Copy the frame — paste it into the description and replace the <placeholders>"
                    onClick={() => { navigator.clipboard.writeText(matches.suggestions!.template!); toast('Template copied — paste it into the description') }}>⧉</button>
                </div>
              )}
            </div>
          )}
          {owns && matches.suggestions?.packages?.length ? (
            <div className={`resolve-card ${checking ? 'rechecking' : ''}`}>
              <div className="resolve-head">
                <span className="dot green" /> Pick a resolution
                <span className="muted" style={{ fontWeight: 400 }}> — meaning unchanged, verified against every product</span>
                {checking && <span className="rescore"><span className="spin" /> re-scoring…</span>}
              </div>
              {matches.suggestions.packages.map((p: any, i: number) => (
                <div key={i}>
                  <div className="pkg-row">
                    <span className="pkg-num">{i + 1}</span>
                    {p.name !== payload.name
                      ? <><b className="score">{p.name}</b><span className="muted">·</span></>
                      : aud ? null : <span className="muted">keep the name —</span>}
                    {p.title && p.title !== payload.title && (
                      <><span className="pkg-title">{p.title}</span><span className="muted">·</span></>
                    )}
                    <button className="small" onClick={() => setShowResDesc(v => v === i ? null : i)}>
                      {showResDesc === i ? 'hide description' : 'description'}</button>
                    <span className="sugg-outcome" style={{ marginLeft: 'auto' }}>
                      <s className="muted">{Math.round(top.score * 100)}%</s>
                      <span className="sugg-arrow">→</span>
                      {p.new_overall < th
                        ? <span className="delta ok">{Math.round(p.new_overall * 100)}% ✓</span>
                        : <span className="delta part">{Math.round(p.new_overall * 100)}%</span>}
                    </span>
                    <button className="icon-act" disabled={checking}
                      title={checking ? 'Re-scoring — one moment'
                        : 'Use this suggestion — fills the fields for you (nothing publishes until Save & publish)'}
                      aria-label={`Use suggestion ${i + 1}`}
                      onClick={() => {
                        set({ name: p.name, title: p.title })
                        const drv = applyDesc(p.description)
                        toast(drv ? `Applied — updated your ${drv} override; re-scoring…`
                                  : 'Applied — re-scoring against every product…') }}>✓</button>
                  </div>
                  {showResDesc === i && <div className="resolve-desc">{p.description}</div>}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </>
  )
}

/* ---------- base form ---------- */
function BaseForm({ payload, set, setSchema, isNew, matches, setPayload, preview, errors, checking, dirty, setOverlay, savedPayload, discardSection, prevTop }: any) {
  const errFor = (frag: string) => (errors ?? []).filter((e: ValidationErr) =>
    e.path.includes(frag) && !e.path.includes('audiences'))
  const restErrors = (errors ?? []).filter((e: ValidationErr) =>
    !['name', 'title', 'description'].some(f => e.path.includes(f)) && !e.path.includes('audiences'))
  const [jsonMode, setJsonMode] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [jsonErr, setJsonErr] = useState('')
  return (
    <div className="card">
      {!isNew && <SectionDraftBanner section="base" draft={payload} published={savedPayload}
        discard={discardSection} />}
      <label>Tool name <span className="req">*</span></label>
      <input value={payload.name} onChange={e => set({ name: e.target.value })} placeholder="get_invoice" />
      {payload.name && !/^[a-zA-Z0-9_-]{1,64}$/.test(payload.name) ? (
        <div className="field-err">Letters, numbers, _ or – only, max 64 characters — the rule MCP clients enforce.</div>
      ) : errFor('name').map((e: ValidationErr, i: number) =>
        <div key={i} className="field-err">{e.message}</div>)}
      <label>Title</label>
      <input value={payload.title ?? ''} onChange={e => set({ title: e.target.value })} placeholder="Get invoice" />
      {errFor('title').map((e: ValidationErr, i: number) =>
        <div key={i} className="field-err">{e.message}</div>)}
      <label>Description <span className="req">*</span></label>
      <textarea value={payload.description} onChange={e => set({ description: e.target.value })} />
      {errFor('description').map((e: ValidationErr, i: number) =>
        <div key={i} className="field-err">{e.message}</div>)}
      <SimilarityWarning matches={matches} checking={checking} payload={payload} set={set} setOverlay={setOverlay} dirty={dirty} prevTop={prevTop} />
      {restErrors.map((e: ValidationErr, i: number) =>
        <div key={i} className="field-err" style={{ marginTop: 8 }}>
          <span className="path">{e.path || 'payload'}</span> — {e.message}</div>)}
      <div className="toolbar" style={{ marginTop: 14 }}>
        <label style={{ margin: 0 }}>Parameters</label>
        <button className="small" onClick={() => {
          if (!jsonMode) setJsonText(JSON.stringify(payload.input_schema, null, 2))
          setJsonErr(''); setJsonMode(!jsonMode)
        }}>{jsonMode ? 'Back to form' : 'Paste / edit JSON'}</button>
        <button className="small" onClick={() => setShowPreview(v => !v)}>
          {showPreview ? 'Hide preview' : 'Live preview'}</button>
      </div>
      {jsonMode ? (
        <>
          <textarea style={{ fontFamily: 'ui-monospace, monospace', minHeight: 260 }}
            value={jsonText} onChange={e => {
              setJsonText(e.target.value)
              let p: any
              try { p = JSON.parse(e.target.value) } catch {
                setJsonErr('Invalid JSON — the last valid state is kept'); return }
              setJsonErr('')
              p = p.payload ?? p
              if (p.inputSchema && !p.input_schema) p = { ...p, input_schema: p.inputSchema }
              if (p.name) {
                setPayload({ name: p.name, title: p.title ?? '', description: p.description ?? '',
                  input_schema: p.input_schema ?? { type: 'object', properties: {} },
                  annotations: p.annotations ?? {}, auth: p.auth ?? {},
                  audiences: p.audiences ?? {} })
              } else if (p.type === 'object' || p.properties) {
                set({ input_schema: p })
              } else setJsonErr('Unrecognized shape — paste a JSON Schema or a full tool definition')
            }} />
          {jsonErr && <div className="err">{jsonErr}</div>}
          <p className="muted">Paste a bare JSON Schema (replaces parameters) or a full tool / MCP
            definition (<code>name, description, inputSchema</code> — fills the whole form).
            Validated live by the preview.</p>
        </>
      ) : (
        <SchemaTree schema={payload.input_schema}
          mutate={fn => setSchema((s: any) => { fn(s); return s })} />
      )}
      {showPreview && <PreviewPanel preview={preview} tab="base" errors={errors} />}

      <label>Behavior hints (MCP annotations — help clients decide when a tool needs confirmation)</label>
      <div className="row">
        {MCP_HINTS.map(h => (
          <label key={h.key} className="switch" title={h.help} style={{ margin: 0 }}>
            <input type="checkbox" style={{ width: 'auto' }}
              checked={payload.annotations?.[h.key] === true}
              onChange={e => {
                const next = { ...(payload.annotations ?? {}) }
                if (e.target.checked) next[h.key] = true
                else delete next[h.key]
                set({ annotations: next })
              }} />
            {h.label}
          </label>
        ))}
      </div>
    </div>
  )
}

/* ---------- audience form: same experience as Base ---------- */

function AudienceForm({ aud, overlay, basePayload, setOverlay, setTab, matches, checking, set, savedPayload, discardSection, prevTop, errors }: any) {
  const ov = overlay.overrides ?? {}
  const enabled = overlay.enabled !== false

  const setOv = (patch: any) => setOverlay(aud, (o: Overlay) => ({ ...o, overrides: { ...(o.overrides ?? {}), ...patch } }))

  return (
    <div className="card">
      <SectionDraftBanner section={aud} draft={basePayload} published={savedPayload}
        discard={discardSection} />
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>Overrides for <span className="pill aud">{aud}</span></h2>
        {!enabled && <span className="pill off">hidden — enable in Manage → Audience access</span>}
      </div>
      {enabled && (<>
        <label>Description for {aud} <span className="muted" style={{ fontWeight: 400 }}>
          {('description' in ov) ? '(overridden)' : '(inheriting external — start typing to override)'}</span></label>
        <textarea value={ov.description ?? basePayload.description ?? ''}
          placeholder={basePayload.description}
          onChange={e => setOv({ description: e.target.value })} />
        {(errors ?? []).filter((e: ValidationErr) => e.path.includes(`audiences/${aud}`))
          .map((e: ValidationErr, i: number) => <div key={i} className="field-err">{e.message}</div>)}
        {('description' in ov) && (
          <button className="small" style={{ marginTop: 4 }} onClick={() =>
            setOverlay(aud, (o: Overlay) => {
              const x = { ...(o.overrides ?? {}) }; delete x.description; return { ...o, overrides: x }
            })}>Reset to external description</button>
        )}
        {('description' in ov) ? (
          <SimilarityWarning matches={matches} checking={checking} payload={basePayload} set={set} setOverlay={setOverlay} aud={aud} prevTop={prevTop} />
        ) : (() => {
          const th = matches?.threshold ?? 0.5
          const top = matches?.matches?.[0]
          return (
            <p className="muted" style={{ margin: '8px 0 0' }}>
              {aud} is served the external description as-is.
              {checking && <span className="rescore" style={{ marginLeft: 8 }}><span className="spin" /> rechecking…</span>}
              {!checking && top && top.score >= th && (
                <> Note: that text currently collides at <b>{Math.round(top.score * 100)}%</b> with{' '}
                  <b className="score">{top.product_key}/{top.name}</b> —{' '}
                  <button className="small" onClick={() => setTab?.('base')}>fix it on the external tab</button></>
              )}
            </p>
          )
        })()}
      </>)}
    </div>
  )
}
