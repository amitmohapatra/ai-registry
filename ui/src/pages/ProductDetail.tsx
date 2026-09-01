import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiKey, Audience, downloadExport, Entity, Member, Product, User } from '../api'
import { toast } from '../App'
import { avatarHue, avatarInitials, ConfirmButton, OverlapPairs, Sentinel, ToolInfo, viewAud } from '../components'
import { PAGE_SIZE, PICKER_LIMIT, SEARCH_DEBOUNCE_MS } from '../config'

export default function ProductDetail({ me }: { me: User | null }) {
  const { productKey = '' } = useParams()
  const [product, setProduct] = useState<Product | null>(null)
  const [tab, setTab] = useState('tools')
  const [dups, setDups] = useState<{ pairs: any[] } | null>(null)
  const [audBump, setAudBump] = useState(0)
  const [manageTab, setManageTab] = useState('members')
  const [loadErr, setLoadErr] = useState<number | null>(null)
  useEffect(() => {
    setLoadErr(null)
    api.product(productKey).then(setProduct)
      .catch(e => setLoadErr(e?.status ?? 500))
  }, [productKey])
  const [scanBump, setScanBump] = useState(0)     // ++ after a threshold change
  useEffect(() => {          // light background scan drives the overlap status dots
    setDups(null); api.duplicates(productKey, 'all').then(setDups).catch(() => {})
  }, [productKey, scanBump])
  const overlapIds = useMemo(() =>
    new Set((dups?.pairs ?? []).flatMap((p: any) => [p.a.id, p.b.id])), [dups])
  if (loadErr) return (
    <div className="card" style={{ maxWidth: 520, margin: '80px auto', textAlign: 'center' }}>
      <h2 style={{ marginTop: 0 }}>{loadErr === 403 ? 'No access to this product' : 'Product not found'}</h2>
      <p className="muted">{loadErr === 403
        ? "You're not a member of this product. Ask one of its admins to add you, or send them a handoff report from the overlap view."
        : 'It may have been deleted, or the link is wrong.'}</p>
      <Link to="/"><button className="primary">Back to products</button></Link>
    </div>
  )
  if (!product) return null
  const canEdit = product.role === 'admin' || product.role === 'super_admin'
  const tabs = ['tools', 'agents', 'overlaps', 'manage', 'audit']
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
      {tab === 'audit' && <Audit productKey={productKey} />}
      {tab === 'manage' && <>
        <div className="seg">
          {[['members', 'Members'], ['audiences', 'Audiences'],
            ['settings', 'Settings']]
            .map(([k, label]) => (
              <button key={k} className={manageTab === k ? 'active' : ''}
                onClick={() => setManageTab(k)}>{label}</button>
            ))}
        </div>
        {manageTab === 'members' && <Members productKey={productKey} canEdit={canEdit} isSuper={product.role === 'super_admin'} />}
        {manageTab === 'audiences' && <>
          <Audiences productKey={productKey} canEdit={canEdit}
            onChanged={() => setAudBump(b => b + 1)} />
          <AudienceAccess productKey={productKey} canEdit={canEdit} refresh={audBump} />
        </>}
        {manageTab === 'settings' && <>
          <Settings productKey={productKey} me={me} canEdit={canEdit} />
          <SimilaritySettings productKey={productKey} canEdit={canEdit} isSuper={me?.is_super_admin ?? false} onChanged={() => setScanBump(b => b + 1)} />
        </>}
      </>}
    </>
  )
}



function Entities({ productKey, type, canEdit, overlapIds }: { productKey: string; type: string; canEdit: boolean; overlapIds?: Set<string> | null }) {
  const [items, setItems] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const topRef = useRef<HTMLDivElement>(null)
  const [loaded, setLoaded] = useState(false)
  const loading = useRef(false)
  const load = (offset: number) => {
    loading.current = true
    return api.entitiesPaged(productKey, { type, q, limit: PAGE_SIZE, offset })
      .then(r => { setItems(prev => offset ? [...prev, ...r.items] : r.items); setTotal(r.total); setLoaded(true) })
      .finally(() => { loading.current = false })
  }
  useEffect(() => { const t = setTimeout(() => load(0), q ? SEARCH_DEBOUNCE_MS : 0); return () => clearTimeout(t) },
    [productKey, type, q])
  const more = () => { if (!loading.current && items.length < total) load(items.length) }
  const remove = async (e: Entity) => {
    await api.deleteEntity(productKey, e.id); toast(`Deleted ${e.name} — live servers updated`); load(0)
  }
  return (
    <>
    <div className="card" ref={topRef}>
      <div className="toolbar">
        <h2>{type === 'tool' ? 'Tools' : 'Agents'}</h2>
        <input className="searchbox" placeholder={`Search ${type}s…`} value={q}
          onChange={e => setQ(e.target.value)} />

        {type === 'tool' && <button className="small" title="Download this product's tools as Excel"
          onClick={() => downloadExport(productKey, 'product')}>⬇ Excel</button>}
        {canEdit && type === 'tool' && <Link to={`/p/${productKey}/tools/new`}><button className="primary small">+ New tool</button></Link>}
      </div>
      <table className="tools-table">
        <thead><tr><th>Name</th><th>Description</th><th>Parameters</th><th>Version</th><th>Audiences</th><th /></tr></thead>
        <tbody>{items.map(e => (
          <tr key={e.id}>
            <td>
              {type === 'tool' && (overlapIds != null ? (overlapIds.has(e.id)
                ? <span className="dot red" title="Overlaps with another tool — see the Overlaps tab" />
                : <span className="dot green" title="No overlaps above the threshold" />)
                : <span className="dot scan" title="Overlap scan running — dots appear when it finishes" />)}
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
      {items.length < total && <Sentinel onHit={more} />}
      {total > PAGE_SIZE && <div className="pager">
        <span>{items.length} of {total} loaded{items.length < total ? ' — scroll for more' : ''}</span>
      </div>}
    </div>
    </>
  )
}

function Duplicates({ productKey }: { productKey: string }) {
  const [scope, setScope] = useState('all')
  const [report, setReport] = useState<{ threshold: number; pairs: any[] } | null>(null)
  useEffect(() => { setReport(null); api.duplicates(productKey, scope).then(setReport) },
    [productKey, scope])
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Overlapping tools</h2>
        <div className="row">
          {report && <span className="muted" title="This product's similarity threshold — adjust in Manage → Settings">
            threshold ≥ {Math.round(report.threshold * 100)}%</span>}
          <select style={{ width: 220 }} value={scope} onChange={e => setScope(e.target.value)}>
            <option value="all">All scopes</option>
            <option value="cross">Cross-product</option>
            <option value="product">Within this product only</option>
          </select>
        </div>
      </div>
      <OverlapPairs report={report} productKey={productKey}
        explain={p => api.explainPair(productKey, p.a.id, p.b.id,
          viewAud(p.a.view), viewAud(p.b.view))}
        resolve={p => api.resolvePair(productKey, p.a.id, p.b.id,
          viewAud(p.a.view), viewAud(p.b.view))} />
    </div>
  )
}

function Members({ productKey, canEdit, isSuper }: { productKey: string; canEdit: boolean; isSuper?: boolean }) {
  const [members, setMembers] = useState<Member[]>([])
  const [email, setEmail] = useState(''); const [role, setRole] = useState('user'); const [err, setErr] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [directory, setDirectory] = useState<{ email: string; name: string }[]>([])
  const [pickOpen, setPickOpen] = useState(false)
  useEffect(() => {              // searched server-side: scales to any org size
    if (!showAdd) return
    const t = setTimeout(() => api.directoryPaged(email, PICKER_LIMIT + members.length)
      .then(r => setDirectory(r.items)).catch(() => {}), email ? SEARCH_DEBOUNCE_MS : 0)
    return () => clearTimeout(t)
  }, [showAdd, email, members.length])
  const candidates = directory.filter(u => !members.some(m => m.email === u.email))
  const load = () => api.members(productKey).then(setMembers)
  useEffect(() => { load() }, [productKey])
  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { await api.upsertMember(productKey, { email, role }); setEmail(''); setShowAdd(false); load() }
    catch (ex: any) { setErr(String(ex.detail ?? 'Failed')) }
  }
  return (
    <div className="card">
      <div className="toolbar">
        <h2>Members</h2>
        {isSuper && <Link to="/users" className="muted" style={{ fontSize: 13 }}>Manage all people →</Link>}
        {canEdit && <button className="primary small" onClick={() => { setShowAdd(true); setErr('') }}>
          + Add member</button>}
      </div>
      {showAdd && canEdit && (
        <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) setShowAdd(false) }}>
        <form className="modal" onSubmit={add}>
          <h3>Add member</h3>
          <p className="sub">Pick an existing account — create new accounts on the People page.</p>
          <label>Person</label>
          <div style={{ position: 'relative' }}>
            <input autoFocus placeholder="Search users by email or name…" value={email}
              autoComplete="off" name="member-picker" spellCheck={false}
              onChange={e => { setEmail(e.target.value); setPickOpen(true) }}
              onFocus={() => setPickOpen(true)}
              onBlur={() => window.setTimeout(() => setPickOpen(false), 150)} />
            {pickOpen && candidates.length > 0 && (
              <div className="combo-list">
                {candidates.slice(0, PICKER_LIMIT).map(u => (
                  <div key={u.email} className="combo-item"
                    onMouseDown={() => { setEmail(u.email); setPickOpen(false) }}>
                    <b className="score">{u.email}</b>
                    <span className="muted" style={{ marginLeft: 8 }}>{u.name}</span>
                  </div>
                ))}
              </div>
            )}
            {pickOpen && candidates.length === 0 && email && (
              <div className="combo-list"><div className="combo-item muted">
                No matching account — create it on the People page first.</div></div>
            )}
          </div>
          <label>Role</label>
          <select value={role} onChange={e => setRole(e.target.value)}>
            <option value="user">user (read-only)</option>
            <option value="admin">admin (can edit tools)</option>
          </select>
          {err && <div className="field-err" style={{ marginTop: 10 }}>{err}</div>}
          <div className="modal-foot">
            <button type="button" className="quiet" onClick={() => setShowAdd(false)}>Cancel</button>
            <button className="primary" disabled={!email}>Add</button>
          </div>
        </form>
        </div>
      )}
      <table className="people-table">
        <colgroup><col /><col style={{ width: 220 }} /><col style={{ width: 48 }} /></colgroup>
        <thead><tr><th>Member</th><th>Role</th><th /></tr></thead>
        <tbody>{members.map(m => (
          <tr key={m.user_id}>
            <td><div className="person">
              <span className="avatar" style={{ background: avatarHue(m.email) }}>{avatarInitials(m.name, m.email)}</span>
              <div className="person-meta"><b>{m.name || m.email.split('@')[0]}</b>
                <span title={m.email}>{m.email}</span></div>
            </div></td>
            <td>{canEdit ? (
              <select style={{ width: 180 }} value={m.role}
                title="Change this member's role — applies immediately"
                onChange={async e => {
                  await api.upsertMember(productKey, { email: m.email, role: e.target.value })
                  toast(`${m.email} is now ${e.target.value}`); load()
                }}>
                <option value="user">user (read-only)</option>
                <option value="admin">admin (can edit tools)</option>
              </select>
            ) : <span className={`pill ${m.role}`}>{m.role}</span>}</td>
            <td>{canEdit && <ConfirmButton icon label="Remove member" confirmLabel="Remove?"
              onConfirm={async () => {
                await api.removeMember(productKey, m.user_id); toast(`Removed ${m.email}`); load()
              }} />}</td></tr>
        ))}</tbody>
      </table>
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
  const [busy, setBusy] = useState(false)
  const load = () => api.audiences(productKey).then(setAudiences)
  useEffect(() => { load() }, [productKey])
  const hasInternal = (audiences ?? []).some(a => a.key === 'internal')
  const toggleInternal = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) await api.addAudience(productKey, { key: 'internal' })
      else await api.deleteAudience(productKey, 'internal')
      toast(on ? 'Internal audience enabled' : 'Internal audience removed — its overrides were stripped from all tools')
      await load(); onChanged?.()
    } catch (ex: any) { toast(String(ex.detail ?? 'Failed')) }
    finally { setBusy(false) }
  }
  return (
    <div className="card">
      <h2>Audiences</h2>
      <p className="muted">Callers request an audience with the <code>x-tool-audience</code> header; it is only honored
        when their credentials carry the <code>audience:&lt;key&gt;</code> scope. Everyone else gets external.</p>
      <div className="aud-row">
        <label className="aud-check"><input type="checkbox" checked disabled /> <b>external</b>
          <span className="muted"> — always available, the default every caller gets</span></label>
      </div>
      <div className="aud-row">
        <label className="aud-check">
          <input type="checkbox" checked={hasInternal} disabled={!canEdit || busy || audiences === null}
            onChange={e => toggleInternal(e.target.checked)} />
          {' '}<b>internal</b>
          <span className="muted"> — enable to let internal callers get their own wording (inherits external until overridden)
            {hasInternal ? '' : ' (unchecking later strips its overrides from every tool)'}</span>
        </label>
      </div>
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
  const [q, setQ] = useState('')
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
  const visible = (entities ?? []).filter(e => !q || e.name.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="card">
      <div className="toolbar">
        <h2>Audience access</h2>
        <input className="searchbox" placeholder="Search tools…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      {entities === null && <p className="muted">Loading…</p>}
      {entities !== null && entities.length === 0 && <p className="muted">No tools yet.</p>}
      {entities !== null && entities.length > 0 && <>
      <p className="muted">Decide which tools each audience can see and call. Content overrides
        (descriptions, parameters) are edited on each tool's audience tabs; every change here
        publishes instantly and is versioned.</p>
      <table>
        <thead><tr><th>Tool</th>{audiences.map(a =>
          <th key={a.id} style={{ textAlign: 'center' }}>{a.key}{a.is_default ? ' *' : ''}</th>)}</tr></thead>
        <tbody>{visible.map(e => (
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

function SimilaritySettings({ productKey, canEdit, isSuper, onChanged }:
  { productKey: string; canEdit: boolean; isSuper: boolean; onChanged?: () => void }) {
  const [pct, setPct] = useState(50)
  const [def_, setDef] = useState(50)
  const [overridden, setOverridden] = useState(false)
  const [applyGlobal, setApplyGlobal] = useState(false)
  const load = () => api.settingsGet(productKey).then((s: any) => {
    setPct(Math.round(s.similarity_threshold * 100))
    setDef(Math.round(s.registry_default * 100))
    setOverridden(!!s.overridden)
  })
  useEffect(() => { load() }, [productKey])
  return (
    <div className="card">
      <h2>Similarity threshold {overridden && <span className="pill aud">product override</span>}</h2>
      <p className="muted">Flags overlaps for this product — its Overlaps tab, editor warnings and
        suggestions — at or above this value. Registry default: <b>{def_}%</b>.
        Product admins can set a different value for this product only.</p>
      <div className="row">
        <input type="range" min={5} max={95} step={5} value={pct} disabled={!canEdit}
          style={{ width: 220 }} onChange={e => setPct(Number(e.target.value))} />
        <b className="score" style={{ width: 46 }}>{pct}%</b>
        {canEdit && <button className="primary small" onClick={async () => {
          await api.settingsSet(productKey, { similarity_threshold: pct / 100,
            scope: applyGlobal ? 'global' : 'product' })
          toast(applyGlobal ? `Registry default set to ${pct}%` : `This product now flags at ${pct}%`)
          load(); onChanged?.()
        }}>Save</button>}
        {canEdit && overridden && <button className="small" onClick={async () => {
          await api.settingsSet(productKey, { similarity_threshold: def_ / 100, reset: true })
          toast(`Back on the registry default (${def_}%)`)
          load(); onChanged?.()
        }}>Reset to registry default</button>}
      </div>
      {isSuper && (
        <label className="switch" style={{ marginTop: 8, display: 'inline-flex', gap: 6 }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={applyGlobal}
            onChange={e => setApplyGlobal(e.target.checked)} />
          <span className="muted">Set as the registry default (applies to every product without
            an override)</span>
        </label>
      )}
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
