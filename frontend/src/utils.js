/** Shared utilities: formatting, severity helpers, field accessors. */

/**
 * Format a number in Indian grouping (lakhs/crores).
 * e.g. 112976.33 → "₹1,12,976.33"
 */
export function fmtINR(value, decimals = 2) {
  if (value == null || isNaN(value)) return '—'
  const fixed = Number(value).toFixed(decimals)
  const [int, dec] = fixed.split('.')
  // Indian grouping: last 3 digits, then groups of 2
  const intStr = int.replace(/^(-?)(\d+)/, (_, sign, digits) => {
    if (digits.length <= 3) return sign + digits
    const last3 = digits.slice(-3)
    const rest = digits.slice(0, -3)
    const grouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')
    return sign + grouped + ',' + last3
  })
  return '₹' + (dec !== undefined ? `${intStr}.${dec}` : intStr)
}

/** Format a percentage to 2 decimal places. */
export function fmtPct(value) {
  if (value == null || isNaN(value)) return '—'
  return Number(value).toFixed(2) + '%'
}

/** Format a number (pp) with sign, e.g. "+2.07 pp" */
export function fmtPP(value) {
  if (value == null || isNaN(value)) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${Number(value).toFixed(2)} pp`
}

/** Severity → Tailwind colour classes for text and border. */
export const SEVERITY_CLASSES = {
  high:           { text: 'text-red-700',    bg: 'bg-red-50',    border: 'border-red-300',   icon: '⚠' },
  medium:         { text: 'text-amber-700',  bg: 'bg-amber-50',  border: 'border-amber-300', icon: '●' },
  low:            { text: 'text-gray-600',   bg: 'bg-gray-50',   border: 'border-gray-300',  icon: '○' },
  info:           { text: 'text-blue-700',   bg: 'bg-blue-50',   border: 'border-blue-300',  icon: 'ℹ' },
  not_applicable: { text: 'text-gray-400',   bg: 'bg-gray-50',   border: 'border-gray-200',  icon: '—' },
}

/** Rule status → label */
export const STATUS_LABEL = {
  pass:           { label: 'Pass',         colour: 'text-green-700',  bg: 'bg-green-50',   border: 'border-green-300'  },
  warn:           { label: 'Review',       colour: 'text-amber-700',  bg: 'bg-amber-50',   border: 'border-amber-300'  },
  fail:           { label: 'Inconsistent', colour: 'text-red-700',    bg: 'bg-red-50',     border: 'border-red-300'    },
  not_applicable: { label: 'N/A',          colour: 'text-gray-400',   bg: 'bg-gray-50',    border: 'border-gray-200'   },
  not_stated:     { label: 'Not stated',   colour: 'text-gray-500',   bg: 'bg-gray-50',    border: 'border-gray-300'   },
}

/** Sort order for severity (high first) */
const SEV_ORDER = { high: 0, medium: 1, low: 2, info: 3, not_applicable: 4 }

export function sortRules(rules) {
  const statusOrder = { fail: 0, warn: 1, not_stated: 2, pass: 3, not_applicable: 4 }
  return [...rules].sort((a, b) => {
    const sA = statusOrder[a.status] ?? 5
    const sB = statusOrder[b.status] ?? 5
    if (sA !== sB) return sA - sB
    return (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9)
  })
}

/** Extract the value from an EvidenceField envelope (or return null). */
export function ev(field) {
  if (field == null) return null
  if (typeof field === 'object' && 'value' in field) return field.value
  return field
}

/** Check if an EvidenceField is missing / low confidence. */
export function isWeak(field) {
  if (!field) return true
  if (field.status === 'not_found') return true
  if (field.confidence != null && field.confidence < 0.7) return true
  if (field.value == null) return true
  return false
}

/** Whether URL contains ?demo=1 */
export const IS_DEMO = new URLSearchParams(window.location.search).get('demo') === '1'
