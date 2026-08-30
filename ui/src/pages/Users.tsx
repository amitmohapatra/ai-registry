import { useEffect, useRef, useState } from 'react'
import { api, User } from '../api'
import { errorText } from '../api'
import { Sentinel } from '../components'
import { PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '../config'
import { validatePassword } from '../validators'

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [form, setForm] = useState({ email: '', name: '', password: '', is_super_admin: false })
  const [err, setErr] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const loading = useRef(false)
  const load = (offset = 0) => {
    loading.current = true
    return api.usersPaged({ q, limit: PAGE_SIZE, offset })
      .then(r => { setUsers(prev => offset ? [...prev, ...r.items] : r.items); setTotal(r.total) })
      .catch(() => {}).finally(() => { loading.current = false })
  }
  useEffect(() => { const t = setTimeout(() => load(0), q ? SEARCH_DEBOUNCE_MS : 0); return () => clearTimeout(t) }, [q])
  const more = () => { if (!loading.current && users.length < total) load(users.length) }

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    const invalid = validatePassword(form.password)
    if (invalid) { setErr(invalid); return }
    try { await api.createUser(form); setForm({ email: '', name: '', password: '', is_super_admin: false }); setShowCreate(false); load(0) }
    catch (ex: any) { setErr(errorText(ex)) }
  }

  return (
    <>
      <div className="topbar"><div><h1>Users</h1>
        <span className="muted">Create accounts, then grant product access from each product's Members tab.</span></div></div>
      <div className="card">
        <div className="toolbar">
          <h2>Users</h2>
          <input className="searchbox" placeholder="Search users…" value={q}
            onChange={e => setQ(e.target.value)} />
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
          <thead><tr><th>Email</th><th>Name</th><th>Role</th></tr></thead>
          <tbody>{users.map(u => (
            <tr key={u.id}><td>{u.email}</td><td>{u.name}</td>
              <td>{u.is_super_admin ? <span className="pill super_admin">super admin</span> : <span className="pill user">member</span>}</td></tr>
          ))}
          {users.length === 0 && <tr><td colSpan={3} className="muted">No users yet.</td></tr>}
          </tbody>
        </table>
        {users.length < total && <Sentinel onHit={more} />}
        {total > PAGE_SIZE && <div className="pager"><span>{users.length} of {total} loaded{users.length < total ? ' — scroll for more' : ''}</span></div>}
      </div>
    </>
  )
}
