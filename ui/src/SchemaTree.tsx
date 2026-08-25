import { useState } from 'react'

/** Recursive JSON Schema tree editor — Stoplight/Postman-style.
 * Any depth: objects get child properties, arrays get an `items` node.
 * Structure, types, descriptions, required flags here; exotic constraints
 * (pattern, enum, min/max…) via the "Edit schema as JSON" escape hatch. */

const TYPES = ['string', 'integer', 'number', 'boolean', 'array', 'object']
const TYPE_INIT: Record<string, any> = {
  string: {}, integer: {}, number: {}, boolean: {},
  array: { items: { type: 'string' } },
  object: { properties: {} },
}

type Mutate = (fn: (root: any) => void) => void

const getAt = (obj: any, path: (string | number)[]) => path.reduce((o, k) => o?.[k], obj)

export default function SchemaTree({ schema, mutate }: { schema: any; mutate: Mutate }) {
  const props = schema?.properties ?? {}
  const required = new Set<string>(schema?.required ?? [])
  return (
    <div className="schema-tree">
      <div className="schema-head">
        <span style={{ width: 18 }} />
        <span className="col-name">name</span><span className="col-type">type</span>
        <span className="col-desc">description</span><span className="col-req">req</span><span style={{ width: 30 }} />
      </div>
      {Object.keys(props).map(name => (
        <SchemaNode key={name} name={name} node={props[name]} path={['properties', name]}
          required={required.has(name)} depth={0} mutate={mutate} inObject />
      ))}
      <AddRow depth={0} onAdd={(name, type, req) => mutate(root => {
        root.properties = root.properties ?? {}
        root.properties[name] = { type, ...structuredClone(TYPE_INIT[type]) }
        if (req) root.required = [...new Set([...(root.required ?? []), name])]
      })} />
    </div>
  )
}

function SchemaNode({ name, node, path, required, depth, mutate, inObject }: {
  name: string; node: any; path: (string | number)[]; required: boolean
  depth: number; mutate: Mutate; inObject: boolean
}) {
  const [open, setOpen] = useState(depth < 2)
  const type = node?.type ?? 'string'
  const nestable = type === 'object' || type === 'array'
  const isItems = name === 'items' && !inObject
  const parentPath = path.slice(0, -1)

  const patch = (fn: (n: any) => void) => mutate(root => { fn(getAt(root, path)) })
  const setType = (t: string) => patch(n => {
    n.type = t
    if (t === 'object' && !n.properties) n.properties = {}
    if (t === 'array' && !n.items) n.items = { type: 'string' }
    if (t !== 'object') delete n.properties
    if (t !== 'array') delete n.items
  })
  const rename = (next: string) => {
    if (!next || next === name) return
    mutate(root => {
      const parent = getAt(root, parentPath)
      if (parent[next]) return                       // name already taken: no-op
      parent[next] = parent[name]; delete parent[name]
      const gp = getAt(root, parentPath.slice(0, -1))
      if (gp?.required) gp.required = gp.required.map((r: string) => (r === name ? next : r))
    })
  }
  const remove = () => mutate(root => {
    const parent = getAt(root, parentPath)
    delete parent[name]
    const gp = getAt(root, parentPath.slice(0, -1))
    if (gp?.required) gp.required = gp.required.filter((r: string) => r !== name)
  })
  const toggleRequired = (on: boolean) => mutate(root => {
    const gp = getAt(root, parentPath.slice(0, -1))
    const req = new Set(gp.required ?? [])
    on ? req.add(name) : req.delete(name)
    gp.required = [...req]
    if (!gp.required.length) delete gp.required
  })

  return (
    <>
      <div className="schema-row" style={{ paddingLeft: depth * 22 }}>
        <button className="tree-toggle" disabled={!nestable}
          onClick={() => setOpen(o => !o)}>{nestable ? (open ? '▾' : '▸') : '·'}</button>
        {isItems
          ? <span className="col-name items-label">items</span>
          : <input className="col-name" defaultValue={name} key={name}
              onBlur={e => rename(e.target.value.trim())} />}
        <select className="col-type" value={type} onChange={e => setType(e.target.value)}>
          {TYPES.map(t => <option key={t}>{t}</option>)}
        </select>
        <input className="col-desc" placeholder="description" value={node?.description ?? ''}
          onChange={e => patch(n => { e.target.value ? n.description = e.target.value : delete n.description })} />
        <span className="col-req">{inObject && !isItems &&
          <input type="checkbox" style={{ width: 'auto' }} checked={required}
            onChange={e => toggleRequired(e.target.checked)} />}</span>
        <span style={{ width: 30 }}>{!isItems &&
          <button className="icon-btn" title="Remove" onClick={remove}>✕</button>}</span>
      </div>
      {nestable && open && type === 'object' && (
        <>
          {Object.keys(node.properties ?? {}).map(child => (
            <SchemaNode key={child} name={child} node={node.properties[child]}
              path={[...path, 'properties', child]}
              required={(node.required ?? []).includes(child)}
              depth={depth + 1} mutate={mutate} inObject />
          ))}
          <AddRow depth={depth + 1} onAdd={(cname, ctype, creq) => patch(n => {
            n.properties = n.properties ?? {}
            n.properties[cname] = { type: ctype, ...structuredClone(TYPE_INIT[ctype]) }
            if (creq) n.required = [...new Set([...(n.required ?? []), cname])]
          })} />
        </>
      )}
      {nestable && open && type === 'array' && (
        <SchemaNode name="items" node={node.items ?? { type: 'string' }}
          path={[...path, 'items']} required={false}
          depth={depth + 1} mutate={mutate} inObject={false} />
      )}
    </>
  )
}

function AddRow({ depth, onAdd }: { depth: number; onAdd: (name: string, type: string, req: boolean) => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState('string')
  const [req, setReq] = useState(false)
  const submit = () => { if (!name.trim()) return; onAdd(name.trim(), type, req); setName(''); setType('string'); setReq(false) }
  return (
    <div className="schema-row add-row" style={{ paddingLeft: depth * 22 }}>
      <span className="tree-toggle" style={{ visibility: 'hidden' }}>·</span>
      <input className="col-name" placeholder="+ property" value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && submit()} />
      <select className="col-type" value={type} onChange={e => setType(e.target.value)}>
        {TYPES.map(t => <option key={t}>{t}</option>)}
      </select>
      <span className="col-desc muted" style={{ fontSize: '.8rem' }}>{name ? 'Enter ↵ or' : ''}</span>
      <span className="col-req"><input type="checkbox" style={{ width: 'auto' }} checked={req}
        onChange={e => setReq(e.target.checked)} title="required" /></span>
      <span style={{ width: 30 }}>{name &&
        <button className="small" style={{ padding: '2px 8px' }} onClick={submit}>Add</button>}</span>
    </div>
  )
}
