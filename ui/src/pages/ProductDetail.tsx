import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiKey, Audience, Entity, Member, Product, User } from '../api'
import { toast } from '../App'
import { ConfirmButton } from '../components'

export default function ProductDetail({ me }: { me: User | null }) {
  const { productKey = '' } = useParams()
  const [product, setProduct] = useState<Product | null>(null)
  const [tab, setTab] = useState('tools')
  useEffect(() => { api.product(productKey).then(setProduct) }, [productKey])
  if (!product) return null
  const canEdit = product.role === 'admin' || product.role === 'super_admin'
  const tabs = ['tools', 'agents', 'manage']
  return (
    <>
      <div className="topbar">
        <div><h1>{product.name}</h1>
          <span className="muted">key <b className="score">{product.key}</b> · seq {product.seq} · your role </span>
          <span className={`pill ${product.role}`}>{product.role}</span></div>
      </div>
      <div className="tabs">{tabs.map(t => (
        <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
      ))}</div>
      {tab === 'tools' && <Entities productKey={productKey} type="tool" canEdit={canEdit} />}
      {tab === 'agents' && <Entities productKey={productKey} type="agent" canEdit={canEdit} />}
      {tab === 'manage' && <>
        <Members productKey={productKey} canEdit={canEdit} />
        <Audiences productKey={productKey} canEdit={canEdit} />
        <AudienceAccess productKey={productKey} canEdit={canEdit} />
        <SimilaritySettings productKey={productKey} canEdit={canEdit} />
        <Settings productKey={productKey} me={me} canEdit={canEdit} />
        <Audit productKey={productKey} />
      </>}
    </>
  )
}

const PAGE = 25

function Entities({ productKey, type, canEdit }: { productKey: string; type: string; canEdit: boolean }) {
  const [items, setItems] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [q, setQ] = useState('')
  const [showOverlaps, setShowOverlaps] = useState(false)
  const topRef = useRef<HTMLDivElement>(null)
  const load = () => api.entitiesPaged(productKey, { type, q, limit: PAGE, offset: page * PAGE })
    .then(r => { setItems(r.items); setTotal(r.total) })
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t) },
    [productKey, type, q, page])
  const remove = async (e: Entity) => {
    await api.deleteEntity(productKey, e.id); toast(`Deleted ${e.name} — live servers updated`); load()
  }
  return (
    <>
    <div className="card" ref={topRef}>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>{type === 'tool' ? 'Tools' : 'Agents'}</h2>
        <div className="row">
          <input className="searchbox" placeholder={`Search ${type}s…`} value={q}
            onChange={e => { setQ(e.target.value); setPage(0) }} />
          {type === 'tool' && <button className="small" onClick={() => setShowOverlaps(v => !v)}>
            {showOverlaps ? 'Hide overlaps' : 'Check overlaps'}</button>}
          {canEdit && type === 'tool' && <Link to={`/p/${productKey}/tools/new`}><button className="primary small">+ New tool</button></Link>}
        </div>
      </div>
      <table>
        <thead><tr><th>Name</th><th>Description</th><th>Version</th><th>Audiences</th><th /></tr></thead>
        <tbody>{items.map(e => (
          <tr key={e.id}>
            <td>{type === 'tool' ? <Link to={`/p/${productKey}/tools/${e.id}`}><b className="score">{e.name}</b></Link> : <b className="score">{e.name}</b>}</td>
            <td className="muted" style={{ maxWidth: 420 }}>{e.payload.description}</td>
            <td className="score">v{e.version}</td>
            <td>{Object.entries(e.resolved).map(([aud, v]: [string, any]) => (
              <span key={aud} className={`pill ${v?.enabled ? 'aud' : 'off'}`} style={{ marginRight: 4 }}>{aud}</span>
            ))}</td>
            <td>{canEdit && <ConfirmButton icon label={`Delete ${type}`} confirmLabel="Delete?"
              onConfirm={() => remove(e)} />}</td>
          </tr>
        ))}
        {items.length === 0 && <tr><td colSpan={5} className="muted">
          {q ? `No ${type}s matching "${q}".` : 'Nothing here yet.'}</td></tr>}
        </tbody>
      </table>
      {total > PAGE && <div className="pager">
        <span>{page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} of {total}</span>
        <button disabled={page === 0} onClick={() => { setPage(p => p - 1); topRef.current?.scrollIntoView({ behavior: 'smooth' }) }}>‹ Prev</button>
        <button disabled={(page + 1) * PAGE >= total} onClick={() => { setPage(p => p + 1); topRef.current?.scrollIntoView({ behavior: 'smooth' }) }}>Next ›</button>
      </div>}
    </div>
    {showOverlaps && <Duplicates productKey={productKey} />}
    </>
  )
}

function Duplicates({ productKey }: { productKey: string }) {
  const [scope, setScope] = useState('all')
  const [threshold, setThreshold] = useState(0)
  const [report, setReport] = useState<{ threshold: number; pairs: any[] } | null>(null)
  const [open, setOpen] = useState<string>('')
  const [explain, setExplain] = useState<any>(null)
  const [aiText, setAiText] = useState('')
  const [aiOn, setAiOn] = useState(false)
  useEffect(() => { api.duplicates(productKey, scope, threshold || undefined).then(setReport) }, [productKey, scope, threshold])
  useEffect(() => { api.aiStatus(productKey).then(s => setAiOn(s.configured)).catch(() => {}) }, [productKey])
  const pct = (s: number) => `${Math.round(s * 100)}%`
  const toggle = async (p: any) => {
    const key = p.a.id + p.b.id
    if (open === key) { setOpen(''); setExplain(null); setAiText(''); return }
    setOpen(key); setExplain(null); setAiText('')
    setExplain(await api.explainPair(productKey, p.a.id, p.b.id).catch(() => null))
  }
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Overlapping tools</h2>
        <div className="row">
          <select style={{ width: 200 }} value={threshold} onChange={e => setThreshold(Number(e.target.value))}>
            <option value={0}>Similarity ≥ default (80%)</option>
            <option value={0.9}>≥ 90% — near-identical</option>
            <option value={0.65}>≥ 65% — likely overlap</option>
            <option value={0.5}>≥ 50% — exploratory</option>
          </select>
          <select style={{ width: 200 }} value={scope} onChange={e => setScope(e.target.value)}>
            <option value="all">Across all products</option>
            <option value="product">Within this product</option>
          </select>
        </div>
      </div>
      <table>
        <thead><tr><th>Tool A</th><th>Tool B</th><th>Similarity</th><th>Cross-product</th></tr></thead>
        <tbody>{(report?.pairs ?? []).flatMap((p, i) => {
          const key = p.a.id + p.b.id
          const rows = [(
            <tr key={i} style={{ cursor: 'pointer' }} onClick={() => toggle(p)}>
              <td><b className="score">{p.a.product_key}/{p.a.name}</b></td>
              <td><b className="score">{p.b.product_key}/{p.b.name}</b></td>
              <td><b className="score">{pct(p.score)}</b> <span className="muted">(cosine {p.score})</span></td>
              <td>{p.cross_product ? <span className="pill on">yes</span> : <span className="pill user">no</span>}
                <span className="muted" style={{ marginLeft: 8 }}>{open === key ? '▾' : '▸'}</span></td>
            </tr>)]
          if (open === key) rows.push(
            <tr key={key + 'x'}><td colSpan={4} style={{ background: '#fafbfc' }}>
              {!explain ? <span className="muted">Analyzing…</span> : <div>
                <div className="row" style={{ gap: 20 }}>
                  {Object.entries(explain.subscores).map(([k, v]: [string, any]) => (
                    <span key={k} className="muted">{k}: <b className="score">{pct(v)}</b></span>))}
                </div>
                {explain.shared.terms.length > 0 && <p className="muted" style={{ margin: '6px 0' }}>
                  Common terms: {explain.shared.terms.map((t: string) =>
                    <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}</p>}
                {explain.shared.parameters.length > 0 && <p className="muted" style={{ margin: '6px 0' }}>
                  Common parameters: {explain.shared.parameters.map((t: string) =>
                    <span key={t} className="param-op" style={{ marginRight: 4 }}>{t}</span>)}</p>}
                {explain.recommendations.map((r: any, j: number) => (
                  <div key={j} className={r.severity === 'high' ? 'err' : 'ok-banner'}
                    style={{ margin: '6px 0' }}>{r.message}</div>))}
                {aiOn && !aiText && <button className="small" onClick={async (e) => {
                  e.stopPropagation()
                  setAiText('…')
                  const r = await api.aiExplain(productKey, p.a.id, p.b.id).catch(() => null)
                  setAiText(r?.analysis ?? 'AI analysis failed')
                }}>✦ Analyze with AI</button>}
                {aiText && aiText !== '…' && <div className="ok-banner" style={{ marginTop: 6 }}>✦ {aiText}</div>}
                {aiText === '…' && <span className="muted">✦ Thinking…</span>}
              </div>}
            </td></tr>)
          return rows
        })}
        {(report?.pairs ?? []).length === 0 && <tr><td colSpan={4} className="muted">No overlapping pairs at this similarity level. 🎉</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function Members({ productKey, canEdit }: { productKey: string; canEdit: boolean }) {
  const [members, setMembers] = useState<Member[]>([])
  const [email, setEmail] = useState(''); const [role, setRole] = useState('user'); const [err, setErr] = useState('')
  const load = () => api.members(productKey).then(setMembers)
  useEffect(() => { load() }, [productKey])
  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { await api.upsertMember(productKey, { email, role }); setEmail(''); load() }
    catch (ex: any) { setErr(String(ex.detail ?? 'Failed')) }
  }
  return (
    <div className="card">
      <h2>Members</h2>
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Role</th><th /></tr></thead>
        <tbody>{members.map(m => (
          <tr key={m.user_id}><td>{m.email}</td><td>{m.name}</td>
            <td><span className={`pill ${m.role}`}>{m.role}</span></td>
            <td>{canEdit && <ConfirmButton icon label="Remove member" confirmLabel="Remove?"
              onConfirm={async () => {
                await api.removeMember(productKey, m.user_id); toast(`Removed ${m.email}`); load()
              }} />}</td></tr>
        ))}</tbody>
      </table>
      {canEdit && (
        <form className="row" style={{ marginTop: 14 }} onSubmit={add}>
          <input style={{ flex: 2 }} placeholder="person@company.com" value={email} onChange={e => setEmail(e.target.value)} />
          <select style={{ flex: 1 }} value={role} onChange={e => setRole(e.target.value)}>
            <option value="user">user (read-only)</option>
            <option value="admin">admin (can edit tools)</option>
          </select>
          <button className="primary">Add / update</button>
          {err && <div className="err" style={{ width: '100%' }}>{err}</div>}
        </form>
      )}
    </div>
  )
}

function Audiences({ productKey, canEdit }: { productKey: string; canEdit: boolean }) {
  const [audiences, setAudiences] = useState<Audience[]>([])
  const [key, setKey] = useState(''); const [err, setErr] = useState('')
  const load = () => api.audiences(productKey).then(setAudiences)
  useEffect(() => { load() }, [productKey])
  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { await api.addAudience(productKey, { key }); setKey(''); load() }
    catch (ex: any) { setErr(String(ex.detail ?? 'Failed — lowercase key, e.g. internal')) }
  }
  return (
    <div className="card">
      <h2>Audiences</h2>
      <p className="muted">Callers request an audience with the <code>x-tool-audience</code> header; it is only honored
        when their credentials carry the <code>audience:&lt;key&gt;</code> scope. Everyone else gets the default.</p>
      <table>
        <thead><tr><th>Key</th><th>Name</th><th>Default</th><th /></tr></thead>
        <tbody>{audiences.map(a => (
          <tr key={a.id}><td className="score">{a.key}</td><td>{a.display_name}</td>
            <td>{a.is_default ? <span className="pill on">default</span> : ''}</td>
            <td>{canEdit && !a.is_default &&
              <ConfirmButton icon label="Delete audience (removes its overrides from all tools)"
                confirmLabel="Delete?" onConfirm={async () => {
                  await api.deleteAudience(productKey, a.key); toast(`Audience ${a.key} deleted`); load()
                }} />}</td></tr>
        ))}</tbody>
      </table>
      {canEdit && (
        <form className="row" style={{ marginTop: 14 }} onSubmit={add}>
          <input style={{ flex: 1 }} placeholder="internal" value={key} onChange={e => setKey(e.target.value)} />
          <button className="primary">Add audience</button>
          {err && <div className="err" style={{ width: '100%' }}>{err}</div>}
        </form>
      )}
    </div>
  )
}

function Settings({ productKey, me, canEdit }: { productKey: string; me: User | null; canEdit: boolean }) {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [fresh, setFresh] = useState('')
  const [copied, setCopied] = useState(false)
  const [channel, setChannelState] = useState({ redis_url: '', channel_prefix: 'registry' })
  const [saved, setSaved] = useState(false)
  const loadKeys = () => api.apiKeys(productKey).then(setKeys).catch(() => {})
  useEffect(() => {
    loadKeys()
    if (me?.is_super_admin) api.channel(productKey).then(setChannelState).catch(() => {})
  }, [productKey])
  const createKey = async () => { const k = await api.createApiKey(productKey); setFresh(k.plaintext!); loadKeys() }
  const saveChannel = async () => { await api.setChannel(productKey, channel); setSaved(true); setTimeout(() => setSaved(false), 2500) }
  return (
    <>
      {canEdit && (
        <div className="card">
          <h2>SDK API keys</h2>
          <p className="muted">Product MCP servers authenticate to the registry with these. The plaintext is shown once.</p>
          {fresh && <div className="ok-banner row" style={{ justifyContent: 'space-between' }}>
            <span>New key (shown once): <b className="score">{fresh}</b></span>
            <button className="small" onClick={() => { navigator.clipboard.writeText(fresh); setCopied(true); toast('API key copied to clipboard'); setTimeout(() => setCopied(false), 2000) }}>
              {copied ? '✓ Copied' : 'Copy'}</button>
          </div>}
          <table>
            <thead><tr><th>Prefix</th><th>Name</th><th>Status</th><th /></tr></thead>
            <tbody>{keys.map(k => (
              <tr key={k.id}><td className="score">{k.prefix}…</td><td>{k.name}</td>
                <td>{k.revoked ? <span className="pill off">revoked</span> : <span className="pill on">active</span>}</td>
                <td>{!k.revoked && <ConfirmButton label="Revoke" confirmLabel="Revoke now?"
                  title="SDKs using this key lose registry access"
                  onConfirm={async () => {
                    await api.revokeApiKey(productKey, k.id); toast(`Key ${k.prefix}… revoked`); loadKeys()
                  }} />}</td></tr>
            ))}</tbody>
          </table>
          <button className="primary small" style={{ marginTop: 10 }} onClick={createKey}>Issue new key</button>
        </div>
      )}
      {me?.is_super_admin && (
        <div className="card">
          <h2>AI gateway — Bifrost (super admin)</h2>
          <p className="muted">OpenAI-compatible gateway URL + virtual key. Powers "Improve with AI"
            and "Analyze with AI". Leave empty to disable AI features for this product.</p>
          <AiConfigForm productKey={productKey} />
        </div>
      )}
      {me?.is_super_admin && (
        <div className="card">
          <h2>Pub/sub channel (super admin)</h2>
          <p className="muted">This product's own Redis. Leave empty to use the registry's built-in SSE stream.</p>
          <label>Redis URL</label>
          <input placeholder="redis://redis.billing.svc:6379/0" value={channel.redis_url}
            onChange={e => setChannelState({ ...channel, redis_url: e.target.value })} />
          <label>Channel prefix</label>
          <input value={channel.channel_prefix}
            onChange={e => setChannelState({ ...channel, channel_prefix: e.target.value })} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={saveChannel}>Save channel config</button>
            {saved && <span className="ok-banner" style={{ margin: 0 }}>Saved — SDKs notified.</span>}
          </div>
        </div>
      )}
    </>
  )
}

function AudienceAccess({ productKey, canEdit }: { productKey: string; canEdit: boolean }) {
  const [entities, setEntities] = useState<Entity[]>([])
  const [audiences, setAudiences] = useState<Audience[]>([])
  const load = () => {
    api.entities(productKey).then(setEntities)
    api.audiences(productKey).then(setAudiences)
  }
  useEffect(() => { load() }, [productKey])
  const isEnabled = (e: Entity, aud: string) => e.payload.audiences?.[aud]?.enabled !== false
  const toggle = async (e: Entity, aud: string, on: boolean) => {
    const payload = structuredClone(e.payload)
    payload.audiences = payload.audiences ?? {}
    payload.audiences[aud] = { ...(payload.audiences[aud] ?? {}), enabled: on }
    await api.updateEntity(productKey, e.id, { payload, note: `${aud} ${on ? 'enabled' : 'disabled'} via audience access` })
    toast(`${e.name}: ${aud} ${on ? 'enabled' : 'disabled'} — live servers updated`)
    load()
  }
  if (!entities.length) return null
  return (
    <div className="card">
      <h2>Audience access</h2>
      <p className="muted">Decide which tools each audience can see and call. Content overrides
        (descriptions, parameters) are edited on each tool's audience tabs; every change here
        publishes instantly and is versioned.</p>
      <table>
        <thead><tr><th>Tool</th>{audiences.map(a =>
          <th key={a.id} style={{ textAlign: 'center' }}>{a.key}{a.is_default ? ' *' : ''}</th>)}</tr></thead>
        <tbody>{entities.map(e => (
          <tr key={e.id}>
            <td><b className="score">{e.name}</b> <span className="muted">v{e.version}</span></td>
            {audiences.map(a => (
              <td key={a.id} style={{ textAlign: 'center' }}>
                <input type="checkbox" style={{ width: 'auto' }} disabled={!canEdit}
                  checked={isEnabled(e, a.key)}
                  onChange={ev => toggle(e, a.key, ev.target.checked)} />
              </td>
            ))}
          </tr>
        ))}</tbody>
      </table>
      <p className="muted" style={{ marginTop: 8 }}>* default audience — served to unauthenticated
        and unentitled callers.</p>
    </div>
  )
}

function SimilaritySettings({ productKey, canEdit }: { productKey: string; canEdit: boolean }) {
  const [pct, setPct] = useState(50)
  useEffect(() => { api.settingsGet(productKey).then(s => setPct(Math.round(s.similarity_threshold * 100))) }, [productKey])
  return (
    <div className="card">
      <h2>Similarity</h2>
      <p className="muted">Tools scoring at or above this are flagged as overlapping — in the
        duplicates report and in the editor's overlap warning.</p>
      <div className="row">
        <input type="range" min={5} max={95} step={5} value={pct} disabled={!canEdit}
          style={{ width: 220 }} onChange={e => setPct(Number(e.target.value))} />
        <b className="score" style={{ width: 46 }}>{pct}%</b>
        {canEdit && <button className="primary small" onClick={async () => {
          await api.settingsSet(productKey, { similarity_threshold: pct / 100 })
          toast(`Overlap threshold set to ${pct}%`)
        }}>Save</button>}
      </div>
    </div>
  )
}

function AiConfigForm({ productKey }: { productKey: string }) {
  const [cfg, setCfg] = useState({ base_url: '', api_key: '', model: '', generate_prompt: '', explain_prompt: '' })
  useEffect(() => { api.aiConfigGet(productKey).then(c => setCfg({ generate_prompt: '', explain_prompt: '', ...c })).catch(() => {}) }, [productKey])
  return (
    <>
      <label>Bifrost base URL</label>
      <input placeholder="http://localhost:8080/v1" value={cfg.base_url}
        onChange={e => setCfg({ ...cfg, base_url: e.target.value })} />
      <label>Virtual key</label>
      <input type="password" placeholder="vk-…" value={cfg.api_key}
        onChange={e => setCfg({ ...cfg, api_key: e.target.value })} />
      <label>Model</label>
      <input placeholder="anthropic/claude-sonnet-4-5" value={cfg.model}
        onChange={e => setCfg({ ...cfg, model: e.target.value })} />
      <label>Generate prompt override (optional — author &amp; version it in your Bifrost prompt store)</label>
      <textarea placeholder="Leave empty to use the built-in prompt" value={cfg.generate_prompt ?? ''}
        onChange={e => setCfg({ ...cfg, generate_prompt: e.target.value })} />
      <label>Explain prompt override (optional)</label>
      <textarea placeholder="Leave empty to use the built-in prompt" value={cfg.explain_prompt ?? ''}
        onChange={e => setCfg({ ...cfg, explain_prompt: e.target.value })} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={async () => {
          await api.aiConfigSet(productKey, cfg); toast('AI gateway saved') }}>Save AI config</button>
      </div>
    </>
  )
}

function Audit({ productKey }: { productKey: string }) {
  const [rows, setRows] = useState<any[]>([])
  useEffect(() => { api.audit(productKey).then(setRows) }, [productKey])
  return (
    <div className="card">
      <h2>Audit log</h2>
      <table>
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}><td className="muted">{new Date(r.at).toLocaleString()}</td>
            <td>{r.actor}</td><td className="score">{r.action}</td><td>{r.target}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}
