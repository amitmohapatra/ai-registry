import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, downloadExport, errorText, Product, User } from '../api'
import { PairBreakdown } from '../components'
import { validateProductKey } from '../validators'
import { toast } from '../App'
import { ConfirmButton } from '../components'

export default function Products({ me, onCreated }: { me: User | null; onCreated?: () => void }) {
  const [products, setProducts] = useState<Product[]>([])
  const [key, setKey] = useState(''); const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [overlaps, setOverlaps] = useState<{ threshold: number; pairs: any[] } | null | 'off'>('off')
  const [openPair, setOpenPair] = useState('')
  const [pairDetail, setPairDetail] = useState<any>(null)
  const load = () => api.products().then(setProducts)
  useEffect(() => { load() }, [])

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    const invalid = validateProductKey(key)
    if (invalid) { setErr(invalid); return }            // same message as the backend
    try { await api.createProduct({ key, name: name || key }); setKey(''); setName(''); load(); onCreated?.() }
    catch (ex: any) { setErr(errorText(ex)) }
  }

  return (
    <>
      <div className="topbar"><div><h1>Products</h1>
        <span className="muted">Each product owns its MCP tools, audiences and pub/sub channel.</span></div>
        {me?.is_super_admin && products.length > 0 && (
          <button className="small" title="Download every product's tools as one Excel file"
            onClick={() => downloadExport(products[0].key, 'all')}>⬇ Export all (Excel)</button>
        )}</div>
      <div className="card">
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
            {products.length === 0 && <tr><td colSpan={4} className="muted">No products yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="toolbar">
          <h2>Overlaps across all products</h2>
          <button className="small" onClick={() => {
            if (overlaps !== 'off') { setOverlaps('off'); return }
            setOverlaps(null); api.duplicatesAll().then(setOverlaps)
          }}>{overlaps === 'off' ? 'Check overlaps' : 'Hide'}</button>
        </div>
        {overlaps === null && <p className="muted">Analyzing all pairs…</p>}
        {overlaps !== 'off' && overlaps !== null && (
          <table>
            <thead><tr><th>Tool A</th><th>Tool B</th><th>Similarity</th><th /></tr></thead>
            <tbody>{overlaps.pairs.length === 0
              ? <tr><td colSpan={4} className="muted">No overlapping pairs at ≥ {Math.round(overlaps.threshold * 100)}%. 🎉</td></tr>
              : overlaps.pairs.flatMap((p: any, i: number) => {
                const k = p.a.id + p.b.id
                const rows = [(
                  <tr key={i} style={{ cursor: 'pointer' }} onClick={async () => {
                    if (openPair === k) { setOpenPair(''); return }
                    setOpenPair(k); setPairDetail(null)
                    setPairDetail(await api.explainPair(p.a.product_key === products[0]?.key ? p.a.product_key : p.a.product_key, p.a.id, p.b.id).catch(() => ({ failed: true })))
                  }}>
                    <td><b className="score">{p.a.product_key}/{p.a.name}</b></td>
                    <td><b className="score">{p.b.product_key}/{p.b.name}</b></td>
                    <td><b className="score">{Math.round(p.score * 100)}%</b></td>
                    <td><span className="muted">{openPair === k ? '▾' : '▸'}</span></td>
                  </tr>)]
                if (openPair === k) rows.push(
                  <tr key={k + 'x'}><td colSpan={4} style={{ background: '#f8f9fa' }}>
                    {!pairDetail ? <span className="muted">Analyzing…</span>
                      : pairDetail.failed ? <span className="muted">Couldn't load this comparison.</span>
                      : <PairBreakdown ex={pairDetail} />}
                  </td></tr>)
                return rows
              })}</tbody>
          </table>
        )}
      </div>
      {me?.is_super_admin && (
        <form className="card" onSubmit={create}>
          <h2>Onboard a product</h2>
          <div className="row">
            <div style={{ flex: 1 }}><label>Key (slug)</label>
              <input value={key} onChange={e => setKey(e.target.value)} placeholder="billing" /></div>
            <div style={{ flex: 2 }}><label>Display name</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="Billing" /></div>
            <button className="primary" style={{ alignSelf: 'end' }}>Create</button>
          </div>
          {err && <div className="err">{err}</div>}
        </form>
      )}
    </>
  )
}
