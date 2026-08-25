import { useEffect, useState } from 'react'
import { api, User } from '../api'

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [form, setForm] = useState({ email: '', name: '', password: '', is_super_admin: false })
  const [err, setErr] = useState('')
  const load = () => api.users().then(setUsers).catch(() => {})
  useEffect(() => { load() }, [])

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { await api.createUser(form); setForm({ email: '', name: '', password: '', is_super_admin: false }); load() }
    catch (ex: any) { setErr(String(ex.detail ?? 'Failed to create user')) }
  }

  return (
    <>
      <div className="topbar"><div><h1>Users</h1>
        <span className="muted">Create accounts, then grant product access from each product's Members tab.</span></div></div>
      <div className="card">
        <table>
          <thead><tr><th>Email</th><th>Name</th><th>Role</th></tr></thead>
          <tbody>{users.map(u => (
            <tr key={u.id}><td>{u.email}</td><td>{u.name}</td>
              <td>{u.is_super_admin ? <span className="pill super_admin">super admin</span> : <span className="pill user">member</span>}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <form className="card" onSubmit={create}>
        <h2>Create user</h2>
        <div className="row">
          <div style={{ flex: 2 }}><label>Email</label>
            <input value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></div>
          <div style={{ flex: 1 }}><label>Name</label>
            <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
          <div style={{ flex: 1 }}><label>Password</label>
            <input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></div>
          <label className="switch" style={{ alignSelf: 'end', paddingBottom: 8 }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={form.is_super_admin}
              onChange={e => setForm({ ...form, is_super_admin: e.target.checked })} /> Super admin
          </label>
          <button className="primary" style={{ alignSelf: 'end' }}>Create</button>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
    </>
  )
}
