import { useEffect, useRef, useState } from 'react'
import { api, User } from '../api'
import { errorText } from '../api'
import { toast } from '../App'
import { ConfirmButton, Sentinel } from '../components'
import { PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '../config'
import { validatePassword } from '../validators'

/* The People console: one place for the super admin to manage every account
   AND its product access — search, filter by product, grant/change/remove
   roles, promote/demote super admins, deactivate. Product admins still use
   their own product's Members tab; this page is super-admin only. */
export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [productFilter, setProductFilter] = useState('')
  const [products, setProducts] = useState<{ key: string; name: string }[]>([])
  const [form, setForm] = useState({ email: '', name: '', password: '', is_super_admin: false })
  const [err, setErr] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [grantFor, setGrantFor] = useState('')          // user id with the grant row open
  const [grant, setGrant] = useState({ product: '', role: 'user' })
  const loading = useRef(false)

  const load = (offset = 0) => {
    loading.current = true
    return api.usersPaged({ q, product: productFilter, limit: PAGE_SIZE, offset })
      .then(r => { setUsers(prev => offset ? [...prev, ...r.items] : r.items); setTotal(r.total) })
      .catch(() => {}).finally(() => { loading.current = false })
  }
  useEffect(() => { const t = setTimeout(() => load(0), q ? SEARCH_DEBOUNCE_MS : 0); return () => clearTimeout(t) },
    [q, productFilter])
  useEffect(() => { api.products().then(setProducts).catch(() => {}) }, [])
  const more = () => { if (!loading.current && users.length < total) load(users.length) }

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    const invalid = validatePassword(form.password)
    if (invalid) { setErr(invalid); return }
    try { await api.createUser(form); setForm({ email: '', name: '', password: '', is_super_admin: false }); setShowCreate(false); load(0) }
    catch (ex: any) { setErr(errorText(ex)) }
  }
  const act = (p: Promise<any>, ok: string) =>
    p.then(() => { toast(ok); load(0) }).catch((ex: any) => toast(errorText(ex)))

  const grantRole = (u: User) => {
    if (!grant.product) return
    act(api.upsertMember(grant.product, { email: u.email, role: grant.role }),
      `${u.email} is now ${grant.role} on ${grant.product}`)
    setGrantFor('')
  }

  return (
    <>
      <div className="topbar"><div><h1>People</h1>
        <span className="muted">Accounts and product access, managed in one place.</span></div></div>
      <div className="card">
        <div className="toolbar">
          <h2>People</h2>
          <input className="searchbox" placeholder="Search people…" value={q}
            onChange={e => setQ(e.target.value)} />
          <select value={productFilter} onChange={e => setProductFilter(e.target.value)}
            style={{ width: 200 }} title="Only show people with access to this product">
            <option value="">All products</option>
            {products.map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
          <button className="primary small" onClick={() => { setShowCreate(v => !v); setErr('') }}>
            {showCreate ? 'Cancel' : '+ Create user'}</button>
        </div>
        {showCreate && (
          <form className="onboard-row" onSubmit={create}>
            <div style={{ flex: 2 }}><label>Email</label>
              <input autoFocus value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></div>
            <div style={{ flex: 1 }}><label>Name</label>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
            <div style={{ flex: 1 }}><label>Password</label>
              <input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></div>
            <label className="switch" style={{ alignSelf: 'end', paddingBottom: 8 }}>
              <input type="checkbox" style={{ width: 'auto' }} checked={form.is_super_admin}
                onChange={e => setForm({ ...form, is_super_admin: e.target.checked })} /> Super admin
            </label>
            <button className="primary" style={{ alignSelf: 'end' }}>Create</button>
            {err && <div className="field-err" style={{ flexBasis: '100%' }}>{err}</div>}
          </form>
        )}
        <table>
          <thead><tr><th>Email</th><th>Name</th><th>Product access</th><th style={{ textAlign: 'right' }}>Account</th></tr></thead>
          <tbody>{users.map(u => (
            <tr key={u.id} style={u.is_active === false ? { opacity: .5 } : undefined}>
              <td><b className="score">{u.email}</b>
                {u.is_active === false && <span className="pill" style={{ marginLeft: 6 }}>deactivated</span>}</td>
              <td>{u.name}</td>
              <td>
                {u.is_super_admin && <span className="pill super_admin" title="Full access to every product">super admin</span>}
                {(u.memberships ?? []).map(m => (
                  <span key={m.product_key} className="pill aud" style={{ marginRight: 4 }} title={m.product_name}>
                    {m.product_key} · {m.role}
                    <a style={{ cursor: 'pointer', marginLeft: 4 }} title={`Remove ${u.email} from ${m.product_key}`}
                      onClick={() => act(api.removeMember(m.product_key, u.id), `Removed from ${m.product_key}`)}>×</a>
                  </span>
                ))}
                {!u.is_super_admin && !(u.memberships ?? []).length && <span className="muted">no access yet</span>}
                {grantFor === u.id ? (
                  <span style={{ display: 'inline-flex', gap: 4, marginLeft: 6 }}>
                    <select value={grant.product} onChange={e => setGrant({ ...grant, product: e.target.value })}>
                      <option value="">product…</option>
                      {products.filter(p => !(u.memberships ?? []).some(m => m.product_key === p.key))
                        .map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
                    </select>
                    <select value={grant.role} onChange={e => setGrant({ ...grant, role: e.target.value })}>
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                    <button className="small primary" onClick={() => grantRole(u)}>Add</button>
                    <button className="small" onClick={() => setGrantFor('')}>✕</button>
                  </span>
                ) : (
                  <button className="small" style={{ marginLeft: 6 }} title="Grant a role on a product"
                    onClick={() => { setGrantFor(u.id); setGrant({ product: '', role: 'user' }) }}>+ product</button>
                )}
              </td>
              <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                {u.is_super_admin
                  ? <ConfirmButton label="Remove super admin" confirmLabel="Confirm demote"
                      title="They keep only their explicit product roles (blocked for the last super admin)"
                      onConfirm={() => act(api.patchUser(u.id, { is_super_admin: false }), `${u.email} is no longer super admin`)} />
                  : <button className="small" title="Full access to every product"
                      onClick={() => act(api.patchUser(u.id, { is_super_admin: true }), `${u.email} is now super admin`)}>
                      Make super admin</button>}
                {u.is_active === false
                  ? <button className="small" onClick={() => act(api.patchUser(u.id, { is_active: true }), `${u.email} reactivated`)}>Activate</button>
                  : <ConfirmButton label="Deactivate" confirmLabel="Confirm"
                      title="Blocks sign-in; access and history are kept"
                      onConfirm={() => act(api.patchUser(u.id, { is_active: false }), `${u.email} deactivated`)} />}
              </td>
            </tr>
          ))}
          {users.length === 0 && <tr><td colSpan={4} className="muted">No matching people.</td></tr>}
          </tbody>
        </table>
        {users.length < total && <Sentinel onHit={more} />}
        {total > PAGE_SIZE && <div className="pager"><span>{users.length} of {total} loaded{users.length < total ? ' — scroll for more' : ''}</span></div>}
      </div>
    </>
  )
}
