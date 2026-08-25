import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api, clearToken, hasToken, Product, User } from './api'
import Login from './pages/Login'
import ProductDetail from './pages/ProductDetail'
import ToolEditor from './pages/ToolEditor'
import UsersPage from './pages/Users'
import Products from './pages/Products'

export function toast(msg: string) { window.dispatchEvent(new CustomEvent('app-toast', { detail: msg })) }

function Toaster() {
  const [msgs, setMsgs] = useState<{ id: number; text: string }[]>([])
  useEffect(() => {
    const on = (e: Event) => {
      const id = Date.now() + Math.random()
      setMsgs(m => [...m, { id, text: (e as CustomEvent).detail }])
      setTimeout(() => setMsgs(m => m.filter(x => x.id !== id)), 2600)
    }
    window.addEventListener('app-toast', on)
    return () => window.removeEventListener('app-toast', on)
  }, [])
  return <div className="toast-wrap">{msgs.map(m => <div key={m.id} className="toast">{m.text}</div>)}</div>
}

export default function App() {
  const [me, setMe] = useState<User | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const loc = useLocation()
  const refresh = () => { api.me().then(setMe).catch(() => {}); api.products().then(setProducts).catch(() => {}) }
  useEffect(() => { if (hasToken()) refresh() }, [loc.pathname === '/login'])

  if (!hasToken() && loc.pathname !== '/login') return <Navigate to="/login" />
  if (loc.pathname === '/login') return <Login onLogin={() => refresh()} />

  return (
    <div className="shell">
      <nav className="side">
        <Link className="brand" to="/">✦ AI Registry</Link>
        <div className="side-section">Products</div>
        {products.map(p => (
          <Link key={p.id} className={`item ${loc.pathname.startsWith(`/p/${p.key}`) ? 'active' : ''}`}
            to={`/p/${p.key}`}>{p.name}</Link>
        ))}
        {me?.is_super_admin && (
          <Link className={`item add ${loc.pathname === '/' ? 'active' : ''}`} to="/">+ Add product</Link>
        )}
        <div className="side-bottom">
          {me?.is_super_admin && (
            <Link className={`item ${loc.pathname === '/users' ? 'active' : ''}`} to="/users">People</Link>
          )}
          <a className="item" href="/login" onClick={() => clearToken()}>Sign out</a>
          {me && <div className="muted" style={{ marginTop: 10, padding: "0 12px" }}>{me.email}</div>}
        </div>
      </nav>
      <Toaster />
      <main className="main">
        <Routes>
          <Route path="/" element={<Products me={me} onCreated={refresh} />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/p/:productKey" element={<ProductDetail me={me} />} />
          <Route path="/p/:productKey/tools/new" element={<ToolEditor />} />
          <Route path="/p/:productKey/tools/:entityId" element={<ToolEditor />} />
        </Routes>
      </main>
    </div>
  )
}
