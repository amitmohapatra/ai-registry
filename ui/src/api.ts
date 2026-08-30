// Typed API client. The UI is a pure client of the registry's REST API —
// point VITE_API_URL anywhere and host this SPA anywhere.
const BASE = import.meta.env.VITE_API_URL || ''

export type Role = 'super_admin' | 'admin' | 'user'
export interface Membership2 { product_key: string; product_name: string; role: string }
export interface User { id: string; email: string; name: string; is_super_admin: boolean; is_active?: boolean; memberships?: Membership2[] }
export interface Product { id: string; key: string; name: string; description: string; seq: number; role: Role }
export interface Audience { id: string; key: string; display_name: string; is_default: boolean }
export interface Member { user_id: string; email: string; name: string; role: string }
export interface ApiKey { id: string; name: string; prefix: string; revoked: boolean; plaintext?: string }
export interface Entity { id: string; type: string; name: string; version: number; payload: any; resolved: any }
export interface ValidationErr { path: string; code: string; message: string }
export interface Similar { id: string; product_key: string; type: string; name: string; score: number; lexical: number }

let token = localStorage.getItem('token') || ''
export const setToken = (t: string) => { token = t; localStorage.setItem('token', t) }
export const clearToken = () => { token = ''; localStorage.removeItem('token') }
export const hasToken = () => !!token

export function errorText(ex: any): string {
  const d = ex?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? JSON.stringify(e)).join('; ')
  if (d?.errors) return d.errors.map((e: any) => `${e.path}: ${e.message}`).join('; ')
  return ex?.message ?? 'Request failed'
}

export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === 'string' ? detail : 'Request failed')
  }
  get validationErrors(): ValidationErr[] { return this.detail?.errors ?? [] }
}

async function req<T>(method: string, path: string, body?: any): Promise<T> {
  const r = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (r.status === 401 && path !== '/v1/auth/login') { clearToken(); location.href = '/login' }
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({})))?.detail)
  return r.status === 204 ? (undefined as T) : r.json()
}

/* list fetch with the X-Total-Count pagination contract: response body stays
   a plain array; the total rides in a header */
async function paged<T>(path: string, params: Record<string, string | number | undefined>):
    Promise<{ items: T[]; total: number }> {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {}))
    if (v !== undefined && v !== '') p.set(k, String(v))
  const qs = p.toString()
  const r = await fetch(BASE + path + (qs ? '?' + qs : ''), {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
  if (r.status === 401) { clearToken(); location.href = '/login' }
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({})))?.detail)
  return { items: await r.json(), total: Number(r.headers.get('X-Total-Count') ?? 0) }
}

export async function downloadExport(pk: string, scope: 'product' | 'all') {
  const r = await fetch(BASE + `/v1/products/${pk}/entities/reports/export?scope=${scope}`,
    { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
  if (!r.ok) throw new ApiError(r.status, 'Export failed')
  const blob = await r.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = scope === 'all' ? 'tools-all-products.xlsx' : `tools-${pk}.xlsx`
  a.click()
  URL.revokeObjectURL(a.href)
}

export const api = {
  login: (email: string, password: string) => req<{ access_token: string; user: User }>('POST', '/v1/auth/login', { email, password }),
  me: () => req<User>('GET', '/v1/auth/me'),
  users: () => req<User[]>('GET', '/v1/auth/users'),
  usersPaged: (opts: { q?: string; product?: string; limit?: number; offset?: number }) =>
    paged<User>('/v1/auth/users', opts as any),
  patchUser: (id: string, body: { name?: string; is_super_admin?: boolean; is_active?: boolean }) =>
    req<User>('PATCH', `/v1/auth/users/${id}`, body),
  directoryPaged: (q: string, limit?: number) =>
    paged<{ email: string; name: string }>('/v1/auth/users/directory', { q, limit }),
  versionsPaged: (pk: string, id: string, opts: { limit?: number; offset?: number }) =>
    paged<any>(`/v1/products/${pk}/entities/${id}/versions`, opts as any),
  directory: () => req<{ email: string; name: string }[]>('GET', '/v1/auth/users/directory'),
  createUser: (u: any) => req<User>('POST', '/v1/auth/users', u),
  products: () => req<Product[]>('GET', '/v1/products'),
  product: (key: string) => req<Product>('GET', `/v1/products/${key}`),
  createProduct: (p: any) => req<Product>('POST', '/v1/products', p),
  deleteProduct: (key: string) => req<void>('DELETE', `/v1/products/${key}`),
  audiences: (pk: string) => req<Audience[]>('GET', `/v1/products/${pk}/audiences`),
  addAudience: (pk: string, a: any) => req<Audience>('POST', `/v1/products/${pk}/audiences`, a),
  deleteAudience: (pk: string, key: string) => req<void>('DELETE', `/v1/products/${pk}/audiences/${key}`),
  members: (pk: string) => req<Member[]>('GET', `/v1/products/${pk}/members`),
  upsertMember: (pk: string, m: any) => req<Member>('PUT', `/v1/products/${pk}/members`, m),
  removeMember: (pk: string, uid: string) => req<void>('DELETE', `/v1/products/${pk}/members/${uid}`),
  apiKeys: (pk: string) => req<ApiKey[]>('GET', `/v1/products/${pk}/api-keys`),
  createApiKey: (pk: string) => req<ApiKey>('POST', `/v1/products/${pk}/api-keys`),
  revokeApiKey: (pk: string, id: string) => req<void>('DELETE', `/v1/products/${pk}/api-keys/${id}`),
  revealApiKey: (pk: string) => req<{ plaintext: string; prefix: string }>('GET', `/v1/products/${pk}/api-keys/reveal`),
  channel: (pk: string) => req<{ redis_url: string; channel_prefix: string }>('GET', `/v1/products/${pk}/channel`),
  setChannel: (pk: string, c: any) => req<void>('PUT', `/v1/products/${pk}/channel`, c),
  entities: (pk: string, type = '') => req<Entity[]>('GET', `/v1/products/${pk}/entities${type ? `?type=${type}` : ''}`),
  entitiesPaged: (pk: string, opts: { type?: string; q?: string; limit?: number; offset?: number }) =>
    paged<Entity>(`/v1/products/${pk}/entities`, opts as any),
  entity: (pk: string, id: string) => req<Entity>('GET', `/v1/products/${pk}/entities/${id}`),
  createEntity: (pk: string, e: any) => req<Entity>('POST', `/v1/products/${pk}/entities`, e),
  updateEntity: (pk: string, id: string, e: any) => req<Entity>('PUT', `/v1/products/${pk}/entities/${id}`, e),
  deleteEntity: (pk: string, id: string) => req<void>('DELETE', `/v1/products/${pk}/entities/${id}`),
  dryRun: (pk: string, e: any) => req<{ valid: boolean; errors: ValidationErr[]; resolved: any }>('POST', `/v1/products/${pk}/entities/dry-run`, e),
  versions: (pk: string, id: string) => req<any[]>('GET', `/v1/products/${pk}/entities/${id}/versions`),
  rollback: (pk: string, id: string, v: number) => req<Entity>('POST', `/v1/products/${pk}/entities/${id}/rollback/${v}`),
  duplicatesAll: (audience = 'all') => req<{ threshold: number; pairs: any[]; audience: string; audience_keys: string[] }>('GET', `/v1/reports/duplicates?audience=${encodeURIComponent(audience)}`),
  duplicates: (pk: string, scope: string, audience = 'all', threshold?: number) => req<{ threshold: number; pairs: any[]; audience: string; audience_keys: string[] }>('GET', `/v1/products/${pk}/entities/reports/duplicates?scope=${scope}&audience=${encodeURIComponent(audience)}${threshold ? `&threshold=${threshold}` : ''}`),
  audit: (pk: string) => req<any[]>('GET', `/v1/products/${pk}/audit`),
  similarPreview: (pk: string, draft: any) => req<{ matches: Similar[]; top_explain: any; threshold?: number; suggestions?: { names: { name: string; title: string; new_overall: number | null }[]; titles: { title: string; new_overall: number | null }[]; descriptions: { text: string; new_overall: number | null; keeps_content?: boolean }[]; bundle?: { name: string; title: string; description: string; new_overall: number } | null; template?: string | null; current_name_match?: number; description_tip?: string | null; resolution_hint?: string | null } | null }>('POST', `/v1/products/${pk}/entities/similar-preview`, draft),
  resolvePair: (pk: string, id: string, otherId: string, audA = '', audB = '') => req<any>('GET', `/v1/products/${pk}/entities/${id}/resolve/${otherId}?aud_a=${encodeURIComponent(audA)}&aud_b=${encodeURIComponent(audB)}`),
  explainPair: (pk: string, id: string, otherId: string, audA = '', audB = '') => req<any>('GET', `/v1/products/${pk}/entities/${id}/explain/${otherId}?aud_a=${encodeURIComponent(audA)}&aud_b=${encodeURIComponent(audB)}`),
  settingsGet: (pk: string) => req<{ similarity_threshold: number }>('GET', `/v1/products/${pk}/settings`),
  settingsSet: (pk: string, s: any) => req<void>('PUT', `/v1/products/${pk}/settings`, s),
}
