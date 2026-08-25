import { useEffect, useRef, useState } from 'react'

/** Two-step inline confirm: first click arms it ("Sure?"), second click within 3s
 * executes. No native dialogs — those are silently swallowed in embedded webviews. */
export function ConfirmButton({ label, confirmLabel = 'Sure?', title, onConfirm, icon = false }:
  { label: string; confirmLabel?: string; title?: string; onConfirm: () => void; icon?: boolean }) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<number>()
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const click = () => {
    if (!armed) {
      setArmed(true)
      timer.current = window.setTimeout(() => setArmed(false), 3000)
      return
    }
    window.clearTimeout(timer.current)
    setArmed(false)
    onConfirm()
  }
  if (icon) return (
    <button className="icon-btn" title={title ?? label} onClick={click}
      style={armed ? { background: '#fef2f2', color: 'var(--danger)', fontWeight: 700 } : undefined}>
      {armed ? confirmLabel : '✕'}
    </button>
  )
  return (
    <button className="small danger" title={title} onClick={click}
      style={armed ? { background: 'var(--danger)', color: '#fff', borderColor: 'var(--danger)' } : undefined}>
      {armed ? confirmLabel : label}
    </button>
  )
}
