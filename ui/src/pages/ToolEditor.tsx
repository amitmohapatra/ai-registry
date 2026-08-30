import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, Similar, ValidationErr } from '../api'
import { toast } from '../App'
import SchemaTree from '../SchemaTree'
import { PairBreakdown } from '../components'

type Param = { name: string; schema: any; required: boolean }
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
  const [savedMatches, setSavedMatches] = useState<Matches | null>(null)
  const debounce = useRef<number>()
  const matchDebounce = useRef<number>()
  const draftKey = `draft:${productKey}:${entityId ?? 'new'}`

  useEffect(() => {
    api.product(productKey).then(p => setCanEdit(p.role === 'admin' || p.role === 'super_admin'))
    api.audiences(productKey).then(a => setAudiences(a.map(x => x.key)))
    if (entityId) {
      sessionStorage.removeItem(draftKey)          // leaving without publishing discards edits:
      api.entity(productKey, entityId).then(e => { // coming back always shows the published tool
        setSavedPayload(e.payload); setVersion(e.version)
        setPayload(e.payload)
        api.similarPreview(productKey, { type: 'tool', suggestions: false,
          payload: { ...e.payload, _entity_id: entityId } })
          .then(setSavedMatches).catch(() => {})              // panel shows PUBLISHED state
      })
      api.versions(productKey, entityId).then(setVersions)
    } else {
      const stored = sessionStorage.getItem(draftKey)
      if (stored) setPayload(JSON.parse(stored))
    }
  }, [productKey, entityId])

  // persist a NEW tool's draft across page changes (existing tools always
  // reload their published state — unpublished edits are intentionally dropped)
  useEffect(() => {
    if (isNew && (payload.name || payload.description))
      sessionStorage.setItem(draftKey, JSON.stringify(payload))
  }, [payload, draftKey, isNew])

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
  useEffect(() => {
    window.clearTimeout(matchDebounce.current)
    if (!payload.name && !payload.description) return
    const tail = String(payload.description ?? '').slice(-1)
    const boundary = tail === '' || /[\s.,;:!?)]/.test(tail)
    matchDebounce.current = window.setTimeout(() => {
      setChecking(true)
      api.similarPreview(productKey, { type: 'tool', suggestions: false,
        payload: { ...payload, _entity_id: entityId } })
        .then(r => setMatches(prev => ({ ...r,
          // keep already-loaded packages while the % updates
          suggestions: r.matches[0] && prev?.suggestions
            && r.matches[0].id === prev.matches[0]?.id ? prev.suggestions : r.suggestions })))
        .catch(() => {}).finally(() => setChecking(false))
    }, boundary ? 600 : 1800)
  }, [payload.name, payload.title, payload.description, payload.input_schema,
      JSON.stringify(payload.audiences ?? {}), productKey])

  // tier 2: fetch resolutions only when flagged, after the draft settles
  useEffect(() => {
    const top = matches?.matches?.[0]
    const th2 = matches?.threshold ?? 0.5
    if (!top || top.score < th2 || matches?.suggestions) return
    const id = window.setTimeout(() => {
      api.similarPreview(productKey, { type: 'tool', suggestions: true,
        payload: { ...payload, _entity_id: entityId } })
        .then(setMatches).catch(() => {})
    }, 900)
    return () => window.clearTimeout(id)
  }, [matches, productKey])

  const params: Param[] = useMemo(() => {
    const props = payload.input_schema?.properties ?? {}
    const req = new Set(payload.input_schema?.required ?? [])
    return Object.entries(props).map(([name, schema]) => ({ name, schema, required: req.has(name) }))
  }, [payload])

  const set = (patch: any) => setPayload((p: any) => ({ ...p, ...patch }))
  const setSchema = (fn: (s: any) => any) => setPayload((p: any) => ({ ...p, input_schema: fn(structuredClone(p.input_schema)) }))
  const setOverlay = (aud: string, fn: (o: Overlay) => Overlay) =>
    setPayload((p: any) => ({ ...p, audiences: { ...p.audiences, [aud]: fn(structuredClone(p.audiences?.[aud] ?? {})) } }))

  const save = async () => {
    setErrors([]); setSaved('')
    try {
      if (isNew) {
        const e = await api.createEntity(productKey, { type: 'tool', payload })
        sessionStorage.removeItem(draftKey)
        nav(`/p/${productKey}/tools/${e.id}`)
      } else {
        const e = await api.updateEntity(productKey, entityId!, { payload })
        sessionStorage.removeItem(draftKey); setSavedPayload(payload)
        api.similarPreview(productKey, { type: 'tool', suggestions: false,
          payload: { ...payload, _entity_id: entityId } })
          .then(setSavedMatches).catch(() => {})              // published -> panel updates now
        setVersion(e.version); setSaved(`Saved as v${e.version} — SDKs updated live.`)
        toast(`Saved v${e.version} — live everywhere`)
        api.versions(productKey, entityId!).then(setVersions)
      }
    } catch (ex) {
      if (ex instanceof ApiError && ex.validationErrors.length) setErrors(ex.validationErrors)
      else setErrors([{ path: '', code: 'error', message: String((ex as any)?.detail ?? 'Save failed') }])
    }
  }

  const errFor = (needle: string) => errors.filter(e => e.path.includes(needle))

  return (
    <>
      <div className="topbar">
        <div><h1>{isNew ? 'New tool' : payload.name} {!isNew && <span className="muted">v{version}</span>}</h1>
          <span className="muted">Edits publish to every running MCP server the moment you save.</span></div>
        <div className="row">
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
      {errors.length > 0 && (
        <div className="err">{errors.map((e, i) => (
          <div key={i}><span className="path">{e.path || 'payload'}</span> — {e.message}</div>
        ))}</div>
      )}
      <div className="tabs">
        <button className={tab === 'base' ? 'active' : ''} onClick={() => setTab('base')}>Base</button>
        {audiences.map(a => (
          <button key={a} className={tab === a ? 'active' : ''} onClick={() => setTab(a)}>
            {a}{payload.audiences?.[a] && Object.keys(payload.audiences[a]).length ? ' •' : ''}</button>
        ))}
      </div>
      <div className="editor-grid">
        <div className="editor-main">
      {tab === 'base' ? (
        <BaseForm payload={payload} set={set} setSchema={setSchema} isNew={isNew}
          matches={matches} setPayload={setPayload} checking={checking}
          dirty={!isNew && savedPayload !== null &&
                 JSON.stringify(payload) !== JSON.stringify(savedPayload)}
          preview={preview} previewTab="base" errors={errors} />
      ) : (
        <AudienceForm aud={tab} overlay={payload.audiences?.[tab] ?? {}} params={params}
          basePayload={payload} setOverlay={setOverlay} errFor={errFor}
          preview={preview} errors={errors} matches={matches} checking={checking} set={set} />
      )}
        </div>
        {!isNew && (
          <aside className="editor-rail">
            {<SimilarPanel entityId={entityId} matches={savedMatches} />}
            {(
            <details className="card collapsible">
              <summary><h2>Version history <span className="muted">({versions.length} version{versions.length === 1 ? '' : 's'} — click to expand)</span></h2></summary>
              <table><tbody>{versions.map(v => (
                <tr key={v.version}><td className="score">v{v.version}</td>
                  <td className="muted">{v.note || '—'}</td>
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
            </details>
      )}
          </aside>
        )}
      </div>
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

/* ---------- similar tools: expandable rows, same experience as Check overlaps ---------- */
function SimilarPanel({ entityId, matches }:
  { entityId?: string; matches: Matches | null }) {
  const [open, setOpen] = useState('')
  const [detail, setDetail] = useState<Record<string, any>>({})
  const th = matches?.threshold ?? 0.5
  const visible = (matches?.matches ?? []).filter(m => m.score >= th)
  if (!matches) return null
  if (visible.length === 0) return (
    <div className="card"><h2><span className="dot green" /> Similar tools <span className="muted">(as published)</span></h2>
      <p className="muted">No overlaps — nothing at or above your {Math.round(th * 100)}% threshold.</p></div>
  )

  const toggle = (m: Similar) => {
    if (open === m.id) { setOpen(''); return }
    setOpen(m.id)
    if (detail[m.id]) return
    // draft-consistent: the SAME breakdown that produced this row's %
    const bd = (m as any).breakdown
    const extra = matches.top_explain?.other?.id === m.id ? matches.top_explain : {}
    setDetail(d => ({ ...d, [m.id]: bd ? { ...extra, ...bd } : (extra.subscores ? extra : { failed: true }) }))
  }

  return (
    <div className="card">
      <h2><span className="dot red" /> Similar tools — {visible.length} at or above {Math.round(th * 100)}%
        <span className="muted"> (as published — updates when you save; the warning above tracks your draft live)</span></h2>
      <table>
        <thead><tr><th>Tool</th><th>Similarity</th><th>Status</th><th /></tr></thead>
        <tbody>{visible.slice(0, 5).flatMap(m => {
        const rows = [(
          <tr key={m.id} style={{ cursor: 'pointer' }} onClick={() => toggle(m)}>
            <td><b className="score">{m.product_key}/{m.name}</b></td>
            <td><b className="score">{Math.round(m.score * 100)}%</b></td>
            <td><span className="pill off">above threshold</span></td>
            <td className="detail-cell">{open === m.id ? '▾ Hide' : '▸ Details'}</td>
          </tr>)]
        if (open === m.id) {
          const ex = detail[m.id]
          rows.push(
            <tr key={m.id + 'x'}><td colSpan={4} style={{ background: '#f8f9fa' }}>
              {!ex ? <span className="muted">{entityId ? 'Analyzing…' : 'Save the tool to compare in detail.'}</span>
                : ex.failed ? <span className="muted">Couldn't load this comparison.</span>
                : <PairBreakdown ex={ex} />}
            </td></tr>)
        }
        return rows
      })}</tbody></table>
    </div>
  )
}

function SimilarityWarning({ matches, checking, set, dirty, aud }: any) {
  const [wasFlagged, setWasFlagged] = useState(false)
  const [showResDesc, setShowResDesc] = useState<number | null>(null)
  const th = matches?.threshold ?? 0.5
  const top = matches?.matches?.[0]
  const flagged = top && top.score >= th
  useEffect(() => { if (flagged) setWasFlagged(true) }, [flagged])
  if (aud && !flagged) return null      // audience tabs only surface problems
  return (
    <>
      {!aud && checking && !matches && <p className="muted" style={{ margin: '6px 0 0' }}>Checking similarity…</p>}
      {flagged && (
        <div className="err" style={{ marginTop: 6 }}>
          ⚠ <b className="score">{Math.round(top.score * 100)}%</b> match with{' '}
          <b className="score">{top.product_key}/{top.name}</b>
          <span className="muted"> (threshold {Math.round(th * 100)}%{dirty ? ' · based on your unsaved edits — the overlap page shows the saved version' : ''})</span>
          {(matches as any).flagged_audience && <span className="muted"> · driven by the <b>{(matches as any).flagged_audience}</b> audience's override text</span>}
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
          {!matches.suggestions && (
            <div className="sugg-row" style={{ marginTop: 8 }}>
              <span className="rescore"><span className="spin" /> finding verified resolutions…</span>
            </div>
          )}
          {matches.suggestions?.packages?.length ? (
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
                    <b className="score">{p.name}</b>
                    <span className="muted">·</span>
                    <span className="pkg-title">{p.title}</span>
                    <span className="muted">·</span>
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
                      title={checking ? 'Re-scoring — one moment' : 'Apply name, title and description'}
                      aria-label={`Apply option ${i + 1}`}
                      onClick={() => { set({ name: p.name, title: p.title, description: p.description })
                        toast('Applied — re-scoring against every product…') }}>✓</button>
                  </div>
                  {showResDesc === i && <div className="resolve-desc">{p.description}</div>}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
      {!flagged && wasFlagged && top && (
        <div className="ok-banner" style={{ marginTop: 6 }}>
          ✓ Looks distinct now — closest match is {top.product_key}/{top.name} at{' '}
          {Math.round(top.score * 100)}%, below your {Math.round(th * 100)}% threshold.
        </div>
      )}
    </>
  )
}

/* ---------- base form ---------- */
function BaseForm({ payload, set, setSchema, isNew, matches, setPayload, preview, errors, checking, dirty }: any) {
  const [jsonMode, setJsonMode] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [jsonErr, setJsonErr] = useState('')
  return (
    <div className="card">
      <label>Tool name * {isNew ? '' : '(rename carefully — handlers bind by name)'}</label>
      <input value={payload.name} onChange={e => set({ name: e.target.value })} placeholder="get_invoice" />
      <label>Title</label>
      <input value={payload.title ?? ''} onChange={e => set({ title: e.target.value })} placeholder="Get invoice" />
      <label>Description * (what the model reads — the most important field)</label>
      <textarea value={payload.description} onChange={e => set({ description: e.target.value })} />
      <SimilarityWarning matches={matches} checking={checking} set={set} dirty={dirty} />
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
const TYPE_DEFAULTS: Record<string, any> = {
  string: { type: 'string' }, integer: { type: 'integer' }, number: { type: 'number' },
  boolean: { type: 'boolean' },
  array: { type: 'array', items: { type: 'string' } },
  object: { type: 'object', properties: {} },
}
const ALL_TYPES = Object.keys(TYPE_DEFAULTS)

function AudienceForm({ aud, overlay, params, basePayload, setOverlay, errFor, preview, errors, matches, checking, set }: any) {
  const [showPreview, setShowPreview] = useState(false)
  const ov = overlay.overrides ?? {}
  const pOps = ov.parameters ?? {}
  const enabled = overlay.enabled !== false
  const [ovJsonMode, setOvJsonMode] = useState(false)
  const [ovJsonText, setOvJsonText] = useState('')
  const [ovJsonErr, setOvJsonErr] = useState('')
  const [add, setAdd] = useState({ name: '', type: 'string', description: '', default: '' })

  const setOv = (patch: any) => setOverlay(aud, (o: Overlay) => ({ ...o, overrides: { ...(o.overrides ?? {}), ...patch } }))
  const setParamOp = (op: string, name: string, value: any | null) => setOverlay(aud, (o: Overlay) => {
    const ovr = { ...(o.overrides ?? {}) }; const po = { ...(ovr.parameters ?? {}) }
    const bucket = { ...(po[op] ?? {}) }
    if (value === null) delete bucket[name]; else bucket[name] = value
    if (Object.keys(bucket).length) po[op] = bucket; else delete po[op]
    if (Object.keys(po).length) ovr.parameters = po; else delete ovr.parameters
    return { ...o, overrides: ovr }
  })
  const opOf = (name: string) => pOps.hide?.[name] ? 'hide' : pOps.modify?.[name] ? 'modify' : 'inherit'

  return (
    <div className="card">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>Overrides for <span className="pill aud">{aud}</span></h2>
        {!enabled && <span className="pill off">hidden — enable in Manage → Audience access</span>}
        <button className="small" onClick={() => {
          if (!ovJsonMode) setOvJsonText(JSON.stringify(overlay, null, 2))
          setOvJsonErr(''); setOvJsonMode(!ovJsonMode)
        }}>{ovJsonMode ? 'Back to form' : 'Paste / edit JSON'}</button>
        <button className="small" onClick={() => setShowPreview(v => !v)}>
          {showPreview ? 'Hide preview' : 'Live preview'}</button>
      </div>
      {showPreview && <PreviewPanel preview={preview} tab={aud} errors={errors} />}
      {ovJsonMode ? (
        <>
          <textarea style={{ fontFamily: 'ui-monospace, monospace', minHeight: 240 }}
            value={ovJsonText} onChange={e => {
              setOvJsonText(e.target.value)
              try { const o = JSON.parse(e.target.value); setOvJsonErr(''); setOverlay(aud, () => o) }
              catch { setOvJsonErr('Invalid JSON — the last valid overlay is kept') }
            }} />
          {ovJsonErr && <div className="err">{ovJsonErr}</div>}
          <p className="muted">Overlay JSON: {'{'}"overrides": {'{'}"description", "parameters":
            {'{'}"add" | "modify" | "hide"{'}'}{'}'}{'}'} — modify accepts any partial JSON Schema.</p>
        </>
      ) : enabled && (<>
        <label>Description for {aud} <span className="muted" style={{ fontWeight: 400 }}>
          {('description' in ov) ? '(overridden)' : '(inheriting base — start typing to override)'}</span></label>
        <textarea value={ov.description ?? basePayload.description ?? ''}
          placeholder={basePayload.description}
          onChange={e => setOv({ description: e.target.value })} />
        {('description' in ov) && (
          <button className="small" style={{ marginTop: 4 }} onClick={() =>
            setOverlay(aud, (o: Overlay) => {
              const x = { ...(o.overrides ?? {}) }; delete x.description; return { ...o, overrides: x }
            })}>Reset to base description</button>
        )}
        <SimilarityWarning matches={matches} checking={checking} set={set} aud={aud} />

        <label style={{ marginTop: 14 }}>Parameters for {aud}</label>
        <table>
          <thead><tr><th>Name</th><th>Treatment</th><th>Details</th></tr></thead>
          <tbody>
            {params.map((p: Param) => {
              const op = opOf(p.name)
              return (
                <tr key={p.name}>
                  <td className="score">{p.name}{p.required ? ' *' : ''}</td>
                  <td>
                    <select value={op} onChange={e => {
                      setParamOp('hide', p.name, null); setParamOp('modify', p.name, null)
                      if (e.target.value === 'hide') setParamOp('hide', p.name, { pin: p.schema.type === 'integer' || p.schema.type === 'number' ? 0 : '' })
                      if (e.target.value === 'modify') setParamOp('modify', p.name, { description: p.schema.description ?? '' })
                    }}>
                      <option value="inherit">inherit</option>
                      <option value="modify">customize</option>
                      <option value="hide">hide</option>
                    </select>
                  </td>
                  <td style={{ width: '55%' }}>
                    {op === 'modify' && <input placeholder={`description for ${aud}`}
                      value={pOps.modify[p.name].description ?? ''}
                      onChange={e => setParamOp('modify', p.name, { ...pOps.modify[p.name], description: e.target.value })} />}
                    {op === 'hide' && <div className="row" title={`${aud} callers never see this parameter; the tool always receives this fixed value — callers cannot override it`}>
                      <span className="param-op">value sent to the tool →</span>
                      <input style={{ flex: 1 }} value={String(pOps.hide[p.name].pin ?? '')}
                        onChange={e => {
                          const raw = e.target.value
                          const v = p.schema.type === 'integer' ? parseInt(raw || '0')
                            : p.schema.type === 'number' ? parseFloat(raw || '0')
                            : p.schema.type === 'boolean' ? raw === 'true' : raw
                          setParamOp('hide', p.name, { pin: Number.isNaN(v as any) ? raw : v })
                        }} />
                    </div>}
                    {errFor(`${aud}/parameters`).filter((e: ValidationErr) => e.path.includes(p.name))
                      .map((e: ValidationErr, i: number) => <div key={i} className="err">{e.message}</div>)}
                  </td>
                </tr>
              )
            })}
            {Object.entries(pOps.add ?? {}).map(([name, schema]: [string, any]) => (
              <tr key={name}>
                <td className="score">{name} <span className="param-op">only {aud}</span></td>
                <td className="muted">{schema.type}</td>
                <td><button className="icon-btn" title="Remove" onClick={() => setParamOp('add', name, null)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 10 }}>
          <input style={{ flex: 1 }} placeholder={`new parameter (visible to ${aud} only)`} value={add.name}
            onChange={e => setAdd({ ...add, name: e.target.value })} />
          <select style={{ width: 100 }} value={add.type} onChange={e => setAdd({ ...add, type: e.target.value })}>
            {ALL_TYPES.map(t => <option key={t}>{t}</option>)}
          </select>
          <input style={{ flex: 1 }} placeholder="description" value={add.description}
            onChange={e => setAdd({ ...add, description: e.target.value })} />
          <input style={{ width: 110 }} placeholder="default" value={add.default}
            onChange={e => setAdd({ ...add, default: e.target.value })} />
          <button className="small" disabled={!add.name} onClick={() => {
            const d = add.type === 'integer' ? parseInt(add.default || '0')
              : add.type === 'number' ? parseFloat(add.default || '0')
              : add.type === 'boolean' ? add.default === 'true'
              : add.type === 'array' ? [] : add.type === 'object' ? {} : add.default
            setParamOp('add', add.name, { ...TYPE_DEFAULTS[add.type], default: d,
              ...(add.description ? { description: add.description } : {}) })
            setAdd({ name: '', type: 'string', description: '', default: '' })
          }}>+ Add</button>
        </div>
      </>)}
    </div>
  )
}
