import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiKey, Audience, downloadExport, Entity, Member, Product, User } from '../api'
import { toast } from '../App'
import { ConfirmButton, OverlapPairs, ToolInfo } from '../components'

export default function ProductDetail({ me }: { me: User | null }) {
  const { productKey = '' } = useParams()
  const [product, setProduct] = useState<Product | null>(null)
  const [tab, setTab] = useState('tools')
  const [dups, setDups] = useState<{ pairs: any[] } | null>(null)
  const [audBump, setAudBump] = useState(0)
  useEffect(() => { api.product(productKey).then(setProduct) }, [productKey])
  useEffect(() => {          // light background scan drives the overlap status dots
    setDups(null); api.duplicates(productKey, 'all').then(setDups).catch(() => {})
  }, [productKey])
  const overlapIds = useMemo(() =>
    new Set((dups?.pairs ?? []).flatMap((p: any) => [p.a.id, p.b.id])), [dups])
  if (!product) return null
  const canEdit = product.role === 'admin' || product.role === 'super_admin'
  const tabs = ['tools', 'agents', 'overlaps', 'manage']
  return (
    <>
      <div className="topbar">
        <div><h1>{product.name}</h1>
          <span className="muted">key <b className="score">{product.key}</b> · seq {product.seq} · your role </span>
          <span className={`pill ${product.role}`}>{product.role}</span></div>
      </div>
      <div className="tabs">{tabs.map(t => (
        <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
          {t[0].toUpperCase() + t.slice(1)}
          {t === 'overlaps' && dups && (dups.pairs.length
            ? <span className="dot red" title={`${dups.pairs.length} overlapping pair${dups.pairs.length === 1 ? '' : 's'}`} />
            : <span className="dot green" title="No overlaps" />)}
        </button>
      ))}</div>
      {tab === 'tools' && <Entities productKey={productKey} type="tool" canEdit={canEdit}
        overlapIds={dups ? overlapIds : null} />}
      {tab === 'agents' && <Entities productKey={productKey} type="agent" canEdit={canEdit} />}
      {tab === 'overlaps' && <Duplicates productKey={productKey} />}
      {tab === 'manage' && <>
        <Members productKey={productKey} canEdit={canEdit} />
        <Audiences productKey={productKey} canEdit={canEdit}
          onChanged={() => setAudBump(b => b + 1)} />
        <AudienceAccess productKey={productKey} canEdit={canEdit} refresh={audBump} />
        <SimilaritySettings productKey={productKey} canEdit={me?.is_super_admin ?? false} />
        <Settings productKey={productKey} me={me} canEdit={canEdit} />
        <Audit productKey={productKey} />
      </>}
    </>
  )
}

const PAGE = 25

function Entities({ productKey, type, canEdit, overlapIds }: { productKey: string; type: string; canEdit: boolean; overlapIds?: Set<string> | null }) {
  const [items, setItems] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [q, setQ] = useState('')
  const topRef = useRef<HTMLDivElement>(null)
  const [loaded, setLoaded] = useState(false)
  const load = () => api.entitiesPaged(productKey, { type, q, limit: PAGE, offset: page * PAGE })
    .then(r => { setItems(r.items); setTotal(r.total); setLoaded(true) })
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t) },
    [productKey, type, q, page])
  const remove = async (e: Entity) => {
    await api.deleteEntity(productKey, e.id); toast(`Deleted ${e.name} — live servers updated`); load()
  }
  return (
    <>
    <div className="card" ref={topRef}>
      <div className="toolbar">
        <h2>{type === 'tool' ? 'Tools' : 'Agents'}</h2>
        <input className="searchbox" placeholder={`Search ${type}s…`} value={q}
          onChange={e => { setQ(e.target.value); setPage(0) }} />

        {type === 'tool' && <button className="small" title="Download this product's tools as Excel"
          onClick={() => downloadExport(productKey, 'product')}>⬇ Excel</button>}
        {canEdit && type === 'tool' && <Link to={`/p/${productKey}/tools/new`}><button className="primary small">+ New tool</button></Link>}
      </div>
      <table className="tools-table">
        <thead><tr><th>Name</th><th>Description</th><th>Parameters</th><th>Version</th><th>Audiences</th><th /></tr></thead>
        <tbody>{items.map(e => (
          <tr key={e.id}>
            <td>
              {type === 'tool' && overlapIds != null && (overlapIds.has(e.id)
                ? <span className="dot red" title="Overlaps with another tool — see the Overlaps tab" />
                : <span className="dot green" title="No overlaps above the threshold" />)}
              {type === 'tool'
                ? <Link to={`/p/${productKey}/tools/${e.id}`}><b className="score">{e.name}</b></Link>
                : <b className="score">{e.name}</b>}
              <ToolInfo payload={e.payload} version={e.version}
                audiences={Object.entries(e.resolved).filter(([, v]: [string, any]) => v?.enabled).map(([a]) => a)} />
              {e.payload.title && <div className="cell-sub" title={e.payload.title}>{e.payload.title}</div>}
            </td>
            <td className="muted">
              <div className="clamp2" title={e.payload.description}>{e.payload.description}</div>
            </td>
            <td><ParamChips schema={e.payload.input_schema} /></td>
            <td className="score">v{e.version}</td>
            <td className="cell-auds">{Object.entries(e.resolved).map(([aud, v]: [string, any]) => (
              <span key={aud} className={`pill ${v?.enabled ? 'aud' : 'off'}`}>{aud}</span>
            ))}</td>
            <td>{canEdit && <ConfirmButton icon label={`Delete ${type}`} confirmLabel="Delete?"
              onConfirm={() => remove(e)} />}</td>
          </tr>
        ))}
        {!loaded && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
        {loaded && items.length === 0 && <tr><td colSpan={6} className="muted">
          {q ? `No ${type}s matching "${q}".` : 'Nothing here yet.'}</td></tr>}
        </tbody>
      </table>
      {total > PAGE && <div className="pager">
        <span>{page * PAGE + 1}–{Math.min((page + 1) * PAGE, total)} of {total}</span>
        <button disabled={page === 0} onClick={() => { setPage(p => p - 1); topRef.current?.scrollIntoView({ behavior: 'smooth' }) }}>‹ Prev</button>
        <button disabled={(page + 1) * PAGE >= total} onClick={() => { setPage(p => p + 1); topRef.current?.scrollIntoView({ behavior: 'smooth' }) }}>Next ›</button>
      </div>}
    </div>
    </>
  )
}

function Duplicates({ productKey }: { productKey: string }) {
  const [scope, setScope] = useState('all')
  const [audience, setAudience] = useState('all')
  const [report, setReport] = useState<{ threshold: number; pairs: any[]; audience_keys?: string[] } | null>(null)
  useEffect(() => { setReport(null); api.duplicates(productKey, scope, audience).then(setReport) },
    [productKey, scope, audience])
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Overlapping tools</h2>
        <div className="row">
          {report && <span className="muted">flagging ≥ {Math.round(report.threshold * 100)}% — adjust in
            Manage → Similarity</span>}
          <select style={{ width: 200 }} value={scope} onChange={e => setScope(e.target.value)}>
            <option value="all">Involving this product</option>
            <option value="product">Within this product only</option>
          </select>
          <select style={{ width: 170 }} value={audience} onChange={e => setAudience(e.target.value)}
            title="Which audience's published text to compare">
            <option value="all">All audiences</option>
            {(report?.audience_keys ?? []).map(a =>
              <option key={a} value={a}>{a} view</option>)}
          </select>
        </div>
      </div>
      <OverlapPairs report={report}
        explain={p => api.explainPair(productKey, p.a.id, p.b.id)} />
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

function ParamChips({ schema }: { schema: any }) {
  const props = Object.keys(schema?.properties ?? {})
  const required = new Set<string>(schema?.required ?? [])
  if (props.length === 0) return <span className="muted">—</span>
  const shown = props.slice(0, 3)
  const json = JSON.stringify(schema, null, 2)
  return (
    <span className="param-chips" title={json}>
      {shown.map(p => (
        <span key={p} className="param-chip">{p}{required.has(p) ? <b>*</b> : null}</span>
      ))}
      {props.length > 3 && <span className="param-chip more">+{props.length - 3}</span>}
    </span>
  )
}

function Audiences({ productKey, canEdit, onChanged }:
  { productKey: string; canEdit: boolean; onChanged?: () => void }) {
  const [audiences, setAudiences] = useState<Audience[] | null>(null)
  const [key, setKey] = useState(''); const [err, setErr] = useState('')
  const load = () => api.audiences(productKey).then(setAudiences)
  useEffect(() => { load() }, [productKey])
  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try {
      const created = await api.addAudience(productKey, { key })
      setKey('')
      setAudiences(a => [...(a ?? []), created])   // instant, no refetch wait
      onChanged?.()
    }
    catch (ex: any) { setErr(String(ex.detail ?? 'Failed — lowercase key, e.g. internal')) }
  }
  return (
    <div className="card">
      <h2>Audiences</h2>
      <p className="muted">Callers request an audience with the <code>x-tool-audience</code> header; it is only honored
        when their credentials carry the <code>audience:&lt;key&gt;</code> scope. Everyone else gets the default.</p>
      <table>
        <thead><tr><th>Key</th><th>Name</th><th>Default</th><th /></tr></thead>
        <tbody>{audiences === null
          ? <tr><td colSpan={4} className="muted">Loading…</td></tr>
          : audiences.map(a => (
          <tr key={a.id}><td className="score">{a.key}</td><td>{a.display_name}</td>
            <td>{a.is_default ? <span className="pill on">default</span> : ''}</td>
            <td>{canEdit && !a.is_default &&
              <ConfirmButton icon label="Delete audience (removes its overrides from all tools)"
                confirmLabel="Delete?" onConfirm={async () => {
                  await api.deleteAudience(productKey, a.key); toast(`Audience ${a.key} deleted`)
                  setAudiences(x => (x ?? []).filter(y => y.id !== a.id)); onChanged?.()
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
          <h2>SDK API key</h2>
          <p className="muted">One active key per product. Your MCP server keeps it as
            <code> REGISTRY_API_KEY</code>. Regenerating replaces it immediately — the old
            key stops working the moment the new one exists.</p>
          {fresh && <div className="ok-banner row" style={{ justifyContent: 'space-between' }}>
            <span>New key (shown once — copy it into your server's env): <b className="score">{fresh}</b></span>
            <button className="small" onClick={() => { navigator.clipboard.writeText(fresh); setCopied(true); toast('API key copied to clipboard'); setTimeout(() => setCopied(false), 2000) }}>
              {copied ? '✓ Copied' : 'Copy'}</button>
          </div>}
          {(() => {
            const active = keys.find(k => !k.revoked)
            return (
              <div className="row">
                {active
                  ? <span>Active key: <b className="score">{active.prefix}…</b> <span className="pill on">active</span></span>
                  : <span className="muted">No active key — issue one to connect an MCP server.</span>}
                {active && <button className="small" onClick={async () => {
                  const r = await api.revealApiKey(productKey).catch(() => null)
                  if (!r) { toast('No copyable key — regenerate once'); return }
                  navigator.clipboard.writeText(r.plaintext); toast('API key copied to clipboard')
                }}>Copy</button>}
                {active
                  ? <ConfirmButton label="Regenerate" confirmLabel="Replace key?"
                      title="The current key stops working immediately"
                      onConfirm={createKey} />
                  : <button className="primary small" onClick={createKey}>Issue key</button>}
                {active && <ConfirmButton label="Revoke" confirmLabel="Revoke now?"
                  title="Your MCP servers lose registry access until a new key is issued"
                  onConfirm={async () => {
                    await api.revokeApiKey(productKey, active.id); toast('Key revoked'); loadKeys()
                  }} />}
              </div>
            )
          })()}
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

function AudienceAccess({ productKey, canEdit, refresh = 0 }:
  { productKey: string; canEdit: boolean; refresh?: number }) {
  const [entities, setEntities] = useState<Entity[] | null>(null)
  const [audiences, setAudiences] = useState<Audience[]>([])
  const load = () => {
    api.entities(productKey).then(setEntities)
    api.audiences(productKey).then(setAudiences)
  }
  useEffect(() => { load() }, [productKey, refresh])
  const isEnabled = (e: Entity, aud: string) => e.payload.audiences?.[aud]?.enabled !== false
  const toggle = async (e: Entity, aud: string, on: boolean) => {
    const payload = structuredClone(e.payload)
    payload.audiences = payload.audiences ?? {}
    payload.audiences[aud] = { ...(payload.audiences[aud] ?? {}), enabled: on }
    await api.updateEntity(productKey, e.id, { payload, note: `${aud} ${on ? 'enabled' : 'disabled'} via audience access` })
    toast(`${e.name}: ${aud} ${on ? 'enabled' : 'disabled'} — live servers updated`)
    load()
  }
  return (
    <div className="card">
      <h2>Audience access</h2>
      {entities === null && <p className="muted">Loading…</p>}
      {entities !== null && entities.length === 0 && <p className="muted">No tools yet.</p>}
      {entities !== null && entities.length > 0 && <>
      <p className="muted">Decide which tools each audience can see and call. Content overrides
        (descriptions, parameters) are edited on each tool's audience tabs; every change here
        publishes instantly and is versioned.</p>
      <table>
        <thead><tr><th>Tool</th>{audiences.map(a =>
          <th key={a.id} style={{ textAlign: 'center' }}>{a.key}{a.is_default ? ' *' : ''}</th>)}</tr></thead>
        <tbody>{(entities ?? []).map(e => (
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
        and unentitled callers.</p></>}
    </div>
  )
}

function SimilaritySettings({ productKey, canEdit }: { productKey: string; canEdit: boolean }) {
  const [pct, setPct] = useState(50)
  useEffect(() => { api.settingsGet(productKey).then(s => setPct(Math.round(s.similarity_threshold * 100))) }, [productKey])
  return (
    <div className="card">
      <h2>Similarity threshold <span className="muted">(whole registry)</span></h2>
      <p className="muted">One threshold for every product and every screen: the overlap report,
        the editor's warnings and the suggestions all use exactly this value. Super admin only.</p>
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
