// Shared client-side validation — mirrors backend rules so users get instant,
// identical feedback (the backend remains the authority; these are UX mirrors).

export const PRODUCT_KEY_RE = /^[a-zA-Z][a-zA-Z0-9_-]{1,63}$/
export const TOOL_NAME_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,127}$/
export const MIN_PASSWORD = 6

export function validateProductKey(key: string): string | null {
  if (!key) return 'Product key is required.'
  if (!PRODUCT_KEY_RE.test(key))
    return 'Product key must start with a letter and may contain letters, numbers, underscores and hyphens.'
  return null
}

export function validatePassword(pw: string): string | null {
  if (pw.length < MIN_PASSWORD) return `Password needs at least ${MIN_PASSWORD} characters.`
  return null
}

export function validateToolName(name: string): string | null {
  if (!name) return 'Tool name is required.'
  if (!TOOL_NAME_RE.test(name))
    return 'Tool name must start with a letter and may contain letters, numbers, underscores and hyphens.'
  return null
}
