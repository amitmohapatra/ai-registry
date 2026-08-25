import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, Similar, ValidationErr } from '../api'
import { toast } from '../App'
import SchemaTree from '../SchemaTree'

type Param = { name: string; schema: any; required: boolean }
type Overlay = { enabled?: boolean; overrides?: any }

const emptyPayload = () => ({
  name: '', description: '',
  input_schema: { type: 'object', properties: {}, required: [] } as any,
  audiences: {} as Record<string, Overlay>,
})

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
  const [aiOn, setAiOn] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiSuggestion, setAiSuggestion] = useState<any>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importErr, setImportErr] = useState('')
  const [matches, setMatches] = useState<{ matches: Similar[]; top_explain: any; threshold?: number } | null>(null)
  const debounce = useRef<number>()
  const matchDebounce = useRef<number>()

  useEffect(() => {
    api.product(productKey).then(p => setCanEdit(p.role === 'admin' || p.role === 'super_admin'))
    api.aiStatus(productKey).then(s => setAiOn(s.configured)).catch(() => {})
    api.audiences(productKey).then(a => setAudiences(a.map(x => x.key)))
    if (entityId) {
      api.entity(productKey, entityId).then(e => { setPayload(e.payload); setVersion(e.version) })
      api.versions(productKey, entityId).then(setVersions)
    }
  }, [productKey, entityId])

  // live preview: the SAME validate+resolve code path as save (dry-run endpoint)
  useEffect(() => {
    window.clearTimeout(debounce.current)
    if (!payload.name) return
    debounce.current = window.setTimeout(async () => {
      try {
        const r = await api.dryRun(productKey, { type: 'tool', payload })
        setErrors(r.errors); setPreview(r.valid ? r.resolved : null)
      } catch { /* network hiccup: keep last preview */ }
    }, 350)
  }, [payload, productKey])

  useEffect(() => {
    window.clearTimeout(matchDebounce.current)
    if (!payload.name && !payload.description) return
    matchDebounce.current = window.setTimeout(() => {
      api.similarPreview(productKey, { type: 'tool',
        payload: { ...payload, _entity_id: entityId } }).then(setMatches).catch(() => {})
    }, 500)
  }, [payload.name, payload.description, payload.input_schema, productKey])

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
        nav(`/p/${productKey}/tools/${e.id}`)
      } else {
        const e = await api.updateEntity(productKey, entityId!, { payload })
        setVersion(e.version); setSaved(`Saved as v${e.version} — SDKs updated live.`); toast(`Saved v${e.version} — live everywhere`)
        api.versions(productKey, entityId!).then(setVersions)
      }
    } catch (ex) {
      if (ex instanceof ApiError && ex.validationErrors.length) setErrors(ex.validationErrors)
      else setErrors([{ path: '', code: 'error', message: String((ex as any)?.detail ?? 'Save failed') }])
    }
  }

  const errFor = (needle: string) => errors.filter(e => e.path.includes(needle))

  const doImport = () => {
    setImportErr('')
    let parsed: any
    try { parsed = JSON.parse(importText) } catch { setImportErr('Not valid JSON.'); return }
    // accept: registry entity export {payload}, full payload, MCP tool {inputSchema}, or bare schema
    let p = parsed.payload ?? parsed
    if (p.inputSchema && !p.input_schema) p = { ...p, input_schema: p.inputSchema }
    if (!p.name && (p.type === 'object' || p.properties)) {
      setPayload((cur: any) => ({ ...cur, input_schema: p }))
      setImportOpen(false); setImportText(''); toast('Schema imported — review the tree below')
      return
    }
    if (!p.name) { setImportErr('Unrecognized shape: expected a tool payload, an MCP tool definition, or a JSON Schema.'); return }
    setPayload({
      name: p.name, title: p.title ?? '', description: p.description ?? '',
      input_schema: p.input_schema ?? { type: 'object', properties: {} },
      annotations: p.annotations ?? {}, auth: p.auth ?? {},
      audiences: p.audiences ?? {},
    })
    setImportOpen(false); setImportText(''); toast(`Imported "${p.name}" — everything is editable below`)
  }

  const aiGenerate = async (instruction = '') => {
    setAiBusy(true); setAiSuggestion(null)
    try {
      const r = await api.aiGenerate(productKey, { type: 'tool',
        payload: { ...payload, _entity_id: entityId }, instruction })
      setAiSuggestion(r.suggestion)
    } catch (ex: any) { toast(String(ex?.detail ?? 'AI request failed')) }
    finally { setAiBusy(false) }
  }
  const aiDifferentiate = () => {
    toast('Asking AI to differentiate the description…')
    aiGenerate('The description overlaps heavily with the similar tools listed. Rewrite it to '
      + 'clearly differentiate this tool: state its distinct scope, data source, and when to '
      + 'prefer it over the similar ones. Keep it 1-3 sentences.')
  }
  const applySuggestion = () => {
    const s = aiSuggestion
    setPayload((p: any) => {
      const schema = structuredClone(p.input_schema ?? { type: 'object', properties: {} })
      for (const [name, d] of Object.entries(s.param_descriptions ?? {}))
        if (schema.properties?.[name]) schema.properties[name].description = d
      return { ...p, description: s.description || p.description,
               title: s.title || p.title, input_schema: schema }
    })
    setAiSuggestion(null); toast('AI suggestion applied — review and save')
  }

  return (
    <>
      <div className="topbar">
        <div><h1>{isNew ? 'New tool' : payload.name} {!isNew && <span className="muted">v{version}</span>}</h1>
          <span className="muted">Edits publish to every running MCP server the moment you save.</span></div>
        <div className="row">
          <button onClick={() => nav(`/p/${productKey}`)}>Back</button>
          {canEdit && <button onClick={() => { setImportOpen(v => !v); setImportErr('') }}>
            {importOpen ? 'Cancel import' : 'Import JSON'}</button>}
          {canEdit
            ? <button className="primary" onClick={save} disabled={errors.length > 0 || !payload.name}>
                {isNew ? 'Create tool' : 'Save & publish'}</button>
            : <span className="pill user">read-only</span>}
        </div>
      </div>
      {importOpen && (
        <div className="card" style={{ borderColor: '#c7d7fe' }}>
          <h2>Import JSON</h2>
          <p className="muted">Paste any of: a full tool payload, an MCP tool definition
            (<code>name / description / inputSchema</code>), a registry export, or a bare JSON Schema
            (replaces parameters only). The form and tree populate from it — nothing saves until you review and hit Save.</p>
          <textarea style={{ fontFamily: 'ui-monospace, monospace', minHeight: 180 }}
            placeholder='{"name": "create_order", "description": "…", "input_schema": { … }}'
            value={importText} onChange={e => setImportText(e.target.value)} autoFocus />
          {importErr && <div className="err">{importErr}</div>}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" disabled={!importText.trim()} onClick={doImport}>Import</button>
          </div>
        </div>
      )}
      {saved && <div className="ok-banner">{saved}</div>}
      {errors.length > 0 && (
        <div className="err">{errors.map((e, i) => (
          <div key={i}><span className="path">{e.path || 'payload'}</span> — {e.message}</div>
        ))}</div>
      )}
      <div className="grid2">
        <div>
          <div className="tabs">
            <button className={tab === 'base' ? 'active' : ''} onClick={() => setTab('base')}>Base</button>
            {audiences.map(a => (
              <button key={a} className={tab === a ? 'active' : ''} onClick={() => setTab(a)}>
                {a}{payload.audiences?.[a] && Object.keys(payload.audiences[a]).length ? ' •' : ''}</button>
            ))}
          </div>
          {tab === 'base' ? (
            <BaseForm payload={payload} params={params} set={set} setSchema={setSchema} isNew={isNew}
              matches={matches} onDifferentiate={canEdit && aiOn ? aiDifferentiate : undefined} />
          ) : (
            <AudienceForm aud={tab} overlay={payload.audiences?.[tab] ?? {}} params={params}
              setOverlay={setOverlay} errFor={errFor} />
          )}
          {!isNew && (
            <div className="card">
              <h2>Version history</h2>
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
                        setSaved(`v${v.version} is now active (published as v${e.version}) — SDKs updated live.`); toast(`v${v.version} is active again`)
                      }}>Make active</button>}</td></tr>
              ))}</tbody></table>
            </div>
          )}
        </div>
        <div>
          {matches && matches.matches.filter(m => m.score >= 0.3).length > 0 && (
            <div className="card">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <h2 style={{ margin: 0 }}>Similar tools <span className="muted">(live, across all products)</span></h2>
                {canEdit && aiOn && <button className="small" disabled={aiBusy} onClick={() => aiGenerate()}>
                  {aiBusy ? 'Thinking…' : '✦ Improve with AI'}</button>}
              </div>
              <table><tbody>{matches.matches.filter(m => m.score >= 0.3).slice(0, 3).map(m => (
                <tr key={m.id}><td><b className="score">{m.product_key}/{m.name}</b></td>
                  <td><b className="score">{Math.round(m.score * 100)}%</b></td>
                  <td>{m.score >= (matches.threshold ?? 0.5)
                    ? <span className="pill off">above threshold</span>
                    : m.score >= (matches.threshold ?? 0.5) * 0.7
                      ? <span className="pill aud">related</span> : null}</td></tr>
              ))}</tbody></table>
              {matches.top_explain && (
                <div style={{ marginTop: 10 }}>
                  {matches.top_explain.shared.terms.length > 0 && (
                    <p className="muted" style={{ margin: '6px 0' }}>Common terms with
                      <b> {matches.top_explain.other.product_key}/{matches.top_explain.other.name}</b>: {' '}
                      {matches.top_explain.shared.terms.slice(0, 8).map((t: string) =>
                        <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}
                    </p>)}
                  {matches.top_explain.shared.parameters.length > 0 && (
                    <p className="muted" style={{ margin: '6px 0' }}>Common parameters: {' '}
                      {matches.top_explain.shared.parameters.map((t: string) =>
                        <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}</p>)}
                  {matches.top_explain.recommendations.map((r: any, i: number) => (
                    <div key={i} className={r.severity === 'high' ? 'err' : 'ok-banner'}
                      style={{ margin: '6px 0' }}>{r.message}</div>))}
                </div>)}
            </div>
          )}
          {aiSuggestion && (
            <div className="card" style={{ borderColor: '#c7d7fe' }}>
              <h2>✦ AI suggestion</h2>
              <label>Description</label>
              <div className="ok-banner" style={{ margin: '4px 0' }}>{aiSuggestion.description}</div>
              {Object.entries(aiSuggestion.param_descriptions ?? {}).map(([k, v]: [string, any]) => (
                <p key={k} className="muted" style={{ margin: '4px 0' }}>
                  <b className="score">{k}</b>: {v}</p>))}
              <div className="row" style={{ marginTop: 10 }}>
                <button className="primary small" onClick={applySuggestion}>Apply to form</button>
                <button className="small" onClick={() => setAiSuggestion(null)}>Dismiss</button>
              </div>
            </div>
          )}
          <div className="card">
            <h2>Live preview — what each audience sees</h2>
            <p className="muted">Rendered by the same validate+resolve pipeline as Save, so it cannot lie.</p>
            <div className="preview">{preview
              ? JSON.stringify(tab === 'base' ? preview : { [tab]: preview[tab] }, null, 2)
              : errors.length ? '⚠ fix the errors to see the preview' : '…'}</div>
          </div>
        </div>
      </div>
    </>
  )
}

const TYPE_DEFAULTS: Record<string, any> = {
  string: { type: 'string' }, integer: { type: 'integer' }, number: { type: 'number' },
  boolean: { type: 'boolean' },
  array: { type: 'array', items: { type: 'string' } },
  object: { type: 'object', properties: {} },
}
const ALL_TYPES = Object.keys(TYPE_DEFAULTS)

const MCP_HINTS = [
  { key: 'readOnlyHint', label: 'Read-only', help: 'Does not modify any state' },
  { key: 'destructiveHint', label: 'Destructive', help: 'May delete or irreversibly change data' },
  { key: 'idempotentHint', label: 'Idempotent', help: 'Safe to call repeatedly with the same args' },
  { key: 'openWorldHint', label: 'Open world', help: 'Interacts with external systems / the internet' },
]

function BaseForm({ payload, params, set, setSchema, isNew, matches, onDifferentiate }: any) {
  const th = matches?.threshold ?? 0.5
  const top = matches?.matches?.[0]
  const flagged = top && top.score >= th
  const [jsonMode, setJsonMode] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [jsonErr, setJsonErr] = useState('')
  return (
    <div className="card">
      <label>Tool name {isNew ? '' : '(rename carefully — handlers bind by name)'}</label>
      <input value={payload.name} onChange={e => set({ name: e.target.value })} placeholder="get_invoice" />
      <label>Title</label>
      <input value={payload.title ?? ''} onChange={e => set({ title: e.target.value })} placeholder="Get invoice" />
      <label>Description (what the model reads — the most important field)</label>
      <textarea value={payload.description} onChange={e => set({ description: e.target.value })} />
      {flagged && (
        <div className="err" style={{ marginTop: 6 }}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <span>⚠ {Math.round(top.score * 100)}% similar to <b className="score">{top.product_key}/{top.name}</b>
              {matches.top_explain?.recommendations?.[0] ? ` — ${matches.top_explain.recommendations[0].message}` : ''}</span>
            {onDifferentiate && <button className="small" onClick={onDifferentiate}
              style={{ flexShrink: 0 }}>✦ Differentiate with AI</button>}
          </div>
        </div>
      )}
      <div className="row" style={{ justifyContent: 'space-between', marginTop: 10 }}>
        <label style={{ margin: 0 }}>Parameters</label>
        <button className="small" onClick={() => {
          if (!jsonMode) setJsonText(JSON.stringify(payload.input_schema, null, 2))
          setJsonErr(''); setJsonMode(!jsonMode)
        }}>{jsonMode ? 'Back to form' : 'Edit schema as JSON (nested/complex)'}</button>
      </div>
      {jsonMode ? (
        <>
          <textarea style={{ fontFamily: 'ui-monospace, monospace', minHeight: 260 }}
            value={jsonText} onChange={e => {
              setJsonText(e.target.value)
              try { const s = JSON.parse(e.target.value); setJsonErr(''); set({ input_schema: s }) }
              catch { setJsonErr('Invalid JSON — fix it to apply (the last valid schema is kept)') }
            }} />
          {jsonErr && <div className="err">{jsonErr}</div>}
          <p className="muted">Full JSON Schema: nested objects, arrays of objects, enums,
            patterns — validated live by the preview.</p>
        </>
      ) : (
        <SchemaTree schema={payload.input_schema}
          mutate={fn => setSchema((s: any) => { fn(s); return s })} />
      )}

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

function AudienceForm({ aud, overlay, params, setOverlay, errFor }: any) {
  const ov = overlay.overrides ?? {}
  const pOps = ov.parameters ?? {}
  const enabled = overlay.enabled !== false
  const [ovJsonMode, setOvJsonMode] = useState(false)
  const [ovJsonText, setOvJsonText] = useState('')
  const [ovJsonErr, setOvJsonErr] = useState('')
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
  const [add, setAdd] = useState({ name: '', type: 'string', description: '', default: '' })

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Overrides for audience: <span className="pill aud">{aud}</span></h2>
        {!enabled && <span className="pill off">hidden from this audience — enable in Manage → Audience access</span>}
      </div>
      {enabled && <>
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
          <button className="small" onClick={() => {
            if (!ovJsonMode) setOvJsonText(JSON.stringify(overlay, null, 2))
            setOvJsonErr(''); setOvJsonMode(!ovJsonMode)
          }}>{ovJsonMode ? 'Back to form' : 'Edit overlay as JSON (complex overrides)'}</button>
        </div>
        {ovJsonMode ? (
          <>
            <textarea style={{ fontFamily: 'ui-monospace, monospace', minHeight: 240 }}
              value={ovJsonText} onChange={e => {
                setOvJsonText(e.target.value)
                try { const o = JSON.parse(e.target.value); setOvJsonErr(''); setOverlay(aud, () => o) }
                catch { setOvJsonErr('Invalid JSON — the last valid overlay is kept') }
              }} />
            {ovJsonErr && <div className="err">{ovJsonErr}</div>}
            <p className="muted">Full overlay JSON: {'{'}"enabled", "overrides": {'{'}"description",
              "parameters": {'{'}"add" | "modify" | "hide"{'}'}{'}'}{'}'} — modify accepts any partial
              JSON Schema (nested subtrees replace). Validated live by the preview.</p>
          </>
        ) : (<>
        <label className="switch" style={{ marginTop: 12 }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={'description' in ov}
            onChange={e => e.target.checked ? setOv({ description: '' }) :
              setOverlay(aud, (o: Overlay) => { const x = { ...(o.overrides ?? {}) }; delete x.description; return { ...o, overrides: x } })} />
          Override description</label>
        {'description' in ov
          ? <textarea value={ov.description} onChange={e => setOv({ description: e.target.value })} />
          : <div className="muted" style={{ padding: '6px 2px' }}>inherits base description</div>}

        <label>Parameters for this audience</label>
        <table>
          <thead><tr><th>Param</th><th>Treatment</th><th /></tr></thead>
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
                      <option value="modify">modify</option>
                      <option value="hide">hide & pin</option>
                    </select>
                  </td>
                  <td style={{ width: '55%' }}>
                    {op === 'modify' && <input placeholder="description for this audience"
                      value={pOps.modify[p.name].description ?? ''}
                      onChange={e => setParamOp('modify', p.name, { ...pOps.modify[p.name], description: e.target.value })} />}
                    {op === 'hide' && <div className="row">
                      <span className="param-op">pinned value →</span>
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
                <td className="score">{name} <span className="param-op">added</span></td>
                <td className="muted">{schema.type}, default {JSON.stringify(schema.default)}</td>
                <td><button className="small danger" onClick={() => setParamOp('add', name, null)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 10 }}>
          <input style={{ flex: 1 }} placeholder="new param (this audience only)" value={add.name}
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
          }}>+ Add param</button>
        </div>
        </>)}
      </>}
    </div>
  )
}
