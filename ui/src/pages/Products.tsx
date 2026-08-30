import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, downloadExport, errorText, Product, User } from '../api'
import { OverlapPairs } from '../components'
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
              <button className="primary small" onClick={() => setShowOnboard(v => !v)}>
                {showOnboard ? 'Cancel' : '+ Onboard product'}</button>
            )}
          </div>
          {showOnboard && me?.is_super_admin && (
            <form className="onboard-row" onSubmit={create}>
              <div style={{ flex: 1 }}><label>Key (slug)</label>
                <input autoFocus value={key} onChange={e => setKey(e.target.value)} placeholder="billing" /></div>
              <div style={{ flex: 2 }}><label>Display name</label>
                <input value={name} onChange={e => setName(e.target.value)} placeholder="Billing" /></div>
              <button className="primary" style={{ alignSelf: 'end' }}>Create</button>
              {err && <div className="err" style={{ flexBasis: '100%' }}>{err}</div>}
            </form>
          )}
          <table>
            <thead><tr><th>Product</th><th>Key</th><th>Your role</th><th>Seq</th><th /></tr></thead>
            <tbody>
              {products.map(p => (
                <tr key={p.id}>
                  <td><Link to={`/p/${p.key}`}><b>{p.name}</b></Link></td>
                  <td className="score">{p.key}</td>
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
              {products.length === 0 && <tr><td colSpan={5} className="muted">No products yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'overlaps' && (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2 style={{ margin: 0 }}>
              {overlaps && (overlaps.pairs.length
                ? <span className="dot red" /> : <span className="dot green" />)}
              Overlapping tools across all products
            </h2>
            {overlaps && <span className="muted">
              {overlaps.pairs.length === 0 ? `none at ≥ ${Math.round(overlaps.threshold * 100)}%`
                : `flagging ≥ ${Math.round(overlaps.threshold * 100)}% — internal-text rows are tagged`}</span>}
          </div>
          {overlaps === null ? <p className="muted">Analyzing all pairs…</p>
            : <OverlapPairs report={overlaps}
                explain={p => api.explainPair(p.a.product_key, p.a.id, p.b.id)} />}
        </div>
      )}
    </>
  )
}
