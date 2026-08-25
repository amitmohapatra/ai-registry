import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken, User } from '../api'

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const r = await api.login(email, password)
      setToken(r.access_token)
      onLogin(r.user)
      nav('/')
    } catch { setErr('Invalid email or password') }
  }

  return (
    <div className="login-wrap">
      <form className="card login-box" onSubmit={submit}>
        <h1>✦ AI Registry</h1>
        <p className="muted">One registry for your MCP tools and agents — managed at runtime.</p>
        <label>Email</label>
        <input value={email} onChange={e => setEmail(e.target.value)} autoFocus />
        <label>Password</label>
        <input type="password" value={password} onChange={e => setPassword(e.target.value)} />
        {err && <div className="err">{err}</div>}
        <button className="primary" style={{ marginTop: 14, width: '100%' }}>Sign in</button>
      </form>
    </div>
  )
}
