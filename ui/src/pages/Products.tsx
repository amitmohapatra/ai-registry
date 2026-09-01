import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, downloadExport, errorText, Product, User } from '../api'
import { avatarHue, OverlapPairs, viewAud } from '../components'
import { validateProductKey } from '../validators'
import { toast } from '../App'
import { ConfirmButton } from '../components'

export default function Products({ me, onCreated }: { me: User | null; onCreated?: () => void }) {
  const [products, setProducts] = useState<Product[]>([])
  const [tab, setTab] = useState('products')
  const [showOnboard, setShowOnboard] = useState(false)
  const [key, setKey] = useState(''); const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [overlaps, setOverlaps] = useState<{ threshold: number; pairs: any[]; audience_keys?: string[] } | null>(null)
  const [prodFilter, setProdFilter] = useState('')      // '' = all products
  const [scopeFilter, setScopeFilter] = useState('all') // all | cross | product
  useEffect(() => { setOverlaps(null); api.duplicatesAll().then(setOverlaps).catch(() => {}) }, [])
  const load = () => api.products().then(setProducts)
  useEffect(() => { load() }, [])

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    const invalid = validateProductKey(key)
    if (invalid) { setErr(invalid); return }            // same message as the backend
    try {
      await api.createProduct({ key, name: name || key })
      setKey(''); setName(''); setShowOnboard(false); load(); onCreated?.()
      toast('Product onboarded')
    } catch (ex: any) { setErr(errorText(ex)) }
  }

  return (
    <>
      <div className="topbar"><div><h1>Products</h1>
        <span className="muted">Each product owns its MCP tools, audiences and pub/sub channel.</span></div>
        {me?.is_super_admin && products.length > 0 && (
          <button className="small" title="Download every product's tools as one Excel file"
            onClick={() => downloadExport(products[0].key, 'all')}>⬇ Export all (Excel)</button>
        )}</div>

      <div className="tabs">
        <button className={tab === 'products' ? 'active' : ''} onClick={() => setTab('products')}>Products</button>
        <button className={tab === 'overlaps' ? 'active' : ''} onClick={() => setTab('overlaps')}>
          Overlaps
          {overlaps && (overlaps.pairs.length
            ? <span className="dot red" title={`${overlaps.pairs.length} overlapping pair${overlaps.pairs.length === 1 ? '' : 's'}`} />
            : <span className="dot green" title="No overlaps" />)}
        </button>
      </div>

      {tab === 'products' && (
        <div className="card">
          <div className="toolbar">
            <h2>Products</h2>
            {me?.is_super_admin && (
              <button className="primary small" onClick={() => setShowOnboard(true)}>+ Onboard product</button>
            )}
          </div>
          <table className="people-table">
            <colgroup><col /><col style={{ width: 140 }} /><col style={{ width: 90 }} /><col style={{ width: 48 }} /></colgroup>
            <thead><tr><th>Product</th><th>Your role</th><th>Seq</th><th /></tr></thead>
            <tbody>
              {products.map(p => (
                <tr key={p.id}>
                  <td><Link to={`/p/${p.key}`} style={{ textDecoration: 'none', color: 'inherit' }}>
                    <div className="person">
                      <span className="avatar" style={{ background: avatarHue(p.key), borderRadius: 8 }}>
                        {p.name[0]?.toUpperCase()}</span>
                      <div className="person-meta"><b>{p.name}</b>
                        <span className="score" style={{ color: 'var(--muted)' }}>{p.key}</span></div>
                    </div>
                  </Link></td>
                  <td><span className={`pill ${p.role}`}>{p.role}</span></td>
                  <td className="score">{p.seq}</td>
                  <td>{me?.is_super_admin && <ConfirmButton icon
                    label={`Delete ${p.name} and ALL its tools, versions, members and keys`}
                    confirmLabel="Delete?" onConfirm={async () => {
                      await api.deleteProduct(p.key)
                      toast(`Product ${p.key} deleted`)
                      load(); onCreated?.()
                    }} />}</td>
                </tr>
              ))}
              {products.length === 0 && <tr><td colSpan={4} className="muted">No products yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {showOnboard && me?.is_super_admin && (
        <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) setShowOnboard(false) }}>
          <form className="modal" onSubmit={create}>
            <h3>Onboard product</h3>
            <p className="sub">The key is the stable slug used in URLs, API routes and SDK config.</p>
            <label>Key (slug)</label>
            <input autoFocus required value={key} onChange={e => setKey(e.target.value)} placeholder="billing" />
            <label>Display name</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="Billing" />
            {err && <div className="field-err" style={{ marginTop: 10 }}>{err}</div>}
            <div className="modal-foot">
              <button type="button" className="quiet" onClick={() => setShowOnboard(false)}>Cancel</button>
              <button className="primary">Create</button>
            </div>
          </form>
        </div>
      )}

      {tab === 'overlaps' && (() => {
        const filtered = overlaps && {
          ...overlaps,
          pairs: overlaps.pairs
            .filter(p => !prodFilter
              || p.a.product_key === prodFilter || p.b.product_key === prodFilter)
            .filter(p => scopeFilter === 'all'
              || (scopeFilter === 'cross' ? p.cross_product : !p.cross_product)),
        }
        return (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
            <h2 style={{ margin: 0 }}>
              {overlaps && (overlaps.pairs.length
                ? <span className="dot red" /> : <span className="dot green" />)}
              Overlapping tools across all products
            </h2>
            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
              {overlaps && overlaps.pairs.length === 0 &&
                <span className="muted" style={{ marginRight: 6 }}>none flagged</span>}
              <select style={{ width: 160 }} value={prodFilter}
                title="Only pairs involving this product"
                onChange={e => setProdFilter(e.target.value)}>
                <option value="">All products</option>
                {products.map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
              </select>
              <select style={{ width: 150 }} value={scopeFilter}
                onChange={e => setScopeFilter(e.target.value)}>
                <option value="all">All</option>
                <option value="cross">Cross-product</option>
                <option value="product">Within product</option>
              </select>
            </span>
          </div>
          {overlaps === null ? <p className="muted">Analyzing all pairs…</p>
            : <OverlapPairs report={filtered}
                explain={p => api.explainPair(p.a.product_key, p.a.id, p.b.id,
                  viewAud(p.a.view), viewAud(p.b.view))}
            resolve={p => api.resolvePair(p.a.product_key, p.a.id, p.b.id,
                  viewAud(p.a.view), viewAud(p.b.view))} />}
        </div>
        )
      })()}
    </>
  )
}
