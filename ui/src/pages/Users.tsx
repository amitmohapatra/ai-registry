import { useEffect, useRef, useState } from 'react'
import { api, User } from '../api'
import { errorText } from '../api'
import { toast } from '../App'
import { avatarHue, avatarInitials, Sentinel } from '../components'
import { ACCESS_CHIP_MAX, PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '../config'
import { validatePassword } from '../validators'


/* The People console: one place for the super admin to manage every account
   and its product access. Product admins keep their product's Members tab —
   this page is super-admin only. */
export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [productFilter, setProductFilter] = useState('')
  const [products, setProducts] = useState<{ key: string; name: string }[]>([])
  const [form, setForm] = useState({ email: '', name: '', password: '', is_super_admin: false })
  const [err, setErr] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [pop, setPop] = useState<{ kind: 'grant' | 'menu' | 'all'; id: string } | null>(null)
  const [grant, setGrant] = useState({ product: '', role: 'user' })
  const [armed, setArmed] = useState('')            // menu item awaiting its confirm click
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
  const closePop = () => { setPop(null); setArmed('') }

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    const invalid = validatePassword(form.password)
    if (invalid) { setErr(invalid); return }
    try { await api.createUser(form); setForm({ email: '', name: '', password: '', is_super_admin: false }); setShowCreate(false); load(0) }
    catch (ex: any) { setErr(errorText(ex)) }
  }
  const act = (p: Promise<any>, ok: string) => {
    closePop()
    p.then(() => { toast(ok); load(0) }).catch((ex: any) => toast(errorText(ex)))
  }
  /* menu items that change access are two-step: first click arms, second commits */
  const confirmItem = (key: string, label: string, confirmLabel: string, danger: boolean, run: () => void) => (
    <button className={danger ? 'danger' : ''}
      onClick={() => armed === key ? run() : setArmed(key)}>
      {armed === key ? confirmLabel : label}</button>
  )

  return (
    <>
      <div className="topbar"><div><h1>People</h1>
        <span className="muted">Accounts and product access, managed in one place.</span></div></div>
      <div className="card">
        <div className="toolbar">
          <h2>People {total > 0 && <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· {total}</span>}</h2>
          <input className="searchbox" placeholder="Search people…" value={q}
            onChange={e => setQ(e.target.value)} />
          <select value={productFilter} onChange={e => setProductFilter(e.target.value)}
            style={{ width: 180 }} title="Only show people with access to this product">
            <option value="">All products</option>
            {products.map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
          <button className="primary small" onClick={() => { setShowCreate(true); setErr('') }}>+ Add person</button>
        </div>
        <table className="people-table">
          <colgroup><col style={{ width: '26%' }} /><col /><col style={{ width: 170 }} /><col style={{ width: 48 }} /></colgroup>
          <thead><tr><th>Person</th><th>Product access</th><th style={{ textAlign: 'right' }}>Account</th><th /></tr></thead>
          <tbody>{users.map(u => (
            <tr key={u.id} style={u.is_active === false ? { opacity: .55 } : undefined}>
              <td>
                <div className="person">
                  <span className="avatar" style={{ background: avatarHue(u.email) }}>{avatarInitials(u.name, u.email)}</span>
                  <div className="person-meta">
                    <b>{u.name || u.email.split('@')[0]}</b>
                    <span title={u.email}>{u.email}</span>
                  </div>
                </div>
              </td>
              <td>
                <div className="chips">
                  {(u.memberships ?? []).slice(0, ACCESS_CHIP_MAX).map(m => (
                    <span key={m.product_key} className="chip" title={`${m.role} on ${m.product_name}`}>
                      {m.product_key} <span className="role">· {m.role}</span>
                      <span className="x" title={`Remove ${u.email} from ${m.product_key}`}
                        onClick={() => act(api.removeMember(m.product_key, u.id), `Removed from ${m.product_key}`)}>×</span>
                    </span>
                  ))}
                  {(u.memberships ?? []).length > ACCESS_CHIP_MAX && (
                    <span className="pop-wrap">
                      <span className="chip more" onClick={() => setPop({ kind: 'all', id: u.id })}>
                        +{(u.memberships ?? []).length - ACCESS_CHIP_MAX} more</span>
                      {pop?.kind === 'all' && pop.id === u.id && (
                        <div className="pop-panel access-list">
                          {(u.memberships ?? []).map(m => (
                            <div key={m.product_key} className="access-row">
                              <b>{m.product_name}</b>
                              <span className="role">{m.role}</span>
                              <span className="x" title={`Remove ${u.email} from ${m.product_key}`}
                                onClick={() => act(api.removeMember(m.product_key, u.id), `Removed from ${m.product_key}`)}>×</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </span>
                  )}
                  {u.is_super_admin && !(u.memberships ?? []).length &&
                    <span className="muted" style={{ fontSize: 12.5 }}>all products via super admin</span>}
                  <span className="pop-wrap">
                    <span className="chip ghost" onClick={() => { setPop({ kind: 'grant', id: u.id }); setGrant({ product: '', role: 'user' }) }}>+ product</span>
                    {pop?.kind === 'grant' && pop.id === u.id && (
                      <div className="pop-panel">
                        <div className="grant-row">
                          <select autoFocus value={grant.product} onChange={e => setGrant({ ...grant, product: e.target.value })}>
                            <option value="">Product…</option>
                            {products.filter(p => !(u.memberships ?? []).some(m => m.product_key === p.key))
                              .map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
                          </select>
                          <select value={grant.role} onChange={e => setGrant({ ...grant, role: e.target.value })}>
                            <option value="user">user (read-only)</option>
                            <option value="admin">admin</option>
                          </select>
                          <button className="primary small" disabled={!grant.product}
                            onClick={() => act(api.upsertMember(grant.product, { email: u.email, role: grant.role }),
                              `${u.email} is now ${grant.role} on ${grant.product}`)}>Add</button>
                        </div>
                      </div>
                    )}
                  </span>
                </div>
              </td>
              <td style={{ textAlign: 'right' }}>
                {u.is_super_admin && <span className="pill super_admin">super admin</span>}
                {u.is_active === false && <span className="pill" style={{ marginLeft: 4 }}>deactivated</span>}
              </td>
              <td style={{ textAlign: 'right' }}>
                <span className="pop-wrap">
                  <button className="kebab" title="Account actions" onClick={() => { setPop({ kind: 'menu', id: u.id }); setArmed('') }}>⋮</button>
                  {pop?.kind === 'menu' && pop.id === u.id && (
                    <div className="pop-panel right menu">
                      {u.is_super_admin
                        ? confirmItem(`super:${u.id}`, 'Remove super admin', 'Confirm — keep only product roles', true,
                            () => act(api.patchUser(u.id, { is_super_admin: false }), `${u.email} is no longer super admin`))
                        : <button onClick={() => act(api.patchUser(u.id, { is_super_admin: true }), `${u.email} is now super admin`)}>
                            Make super admin</button>}
                      {u.is_active === false
                        ? <button onClick={() => act(api.patchUser(u.id, { is_active: true }), `${u.email} reactivated`)}>Reactivate</button>
                        : confirmItem(`off:${u.id}`, 'Deactivate', 'Confirm — blocks sign-in', true,
                            () => act(api.patchUser(u.id, { is_active: false }), `${u.email} deactivated`))}
                    </div>
                  )}
                </span>
              </td>
            </tr>
          ))}
          {users.length === 0 && <tr><td colSpan={4} className="muted">No matching people.</td></tr>}
          </tbody>
        </table>
        {users.length < total && <Sentinel onHit={more} />}
        {total > PAGE_SIZE && <div className="pager"><span>{users.length} of {total} loaded{users.length < total ? ' — scroll for more' : ''}</span></div>}
      </div>
      {pop && <div className="pop-backdrop" onClick={closePop} />}
      {showCreate && (
        <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) setShowCreate(false) }}>
          <form className="modal" onSubmit={create}>
            <h3>Add person</h3>
            <p className="sub">They sign in with this email; grant product access from their row afterwards.</p>
            <label>Email</label>
            <input autoFocus type="email" required value={form.email}
              onChange={e => setForm({ ...form, email: e.target.value })} />
            <label>Name</label>
            <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
            <label>Password</label>
            <input type="password" required value={form.password}
              onChange={e => setForm({ ...form, password: e.target.value })} />
            <label className="check" style={{ fontWeight: 400, color: 'var(--text)' }}>
              <input type="checkbox" checked={form.is_super_admin}
                onChange={e => setForm({ ...form, is_super_admin: e.target.checked })} />
              <span>Super admin
                <span className="hint">Full access to every product and this console</span></span>
            </label>
            {err && <div className="field-err" style={{ marginTop: 10 }}>{err}</div>}
            <div className="modal-foot">
              <button type="button" className="quiet" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="primary">Create</button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}
