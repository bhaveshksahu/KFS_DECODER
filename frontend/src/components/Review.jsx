/**
 * Review screen — inspect and correct extracted fields before analysis.
 *
 * Props:
 *   extraction  : { extraction_id, extraction: KFSExtraction, ... }
 *   onConfirm(extractionId) : proceed to analysis (after optional PATCH)
 *   onBack()                : return to landing
 *
 * Features:
 *   - Each critical field is editable inline; shows evidence (page + quote) on focus
 *   - "Edited by you" badge appears after any change
 *   - On confirm, PATCHes all dirty fields before calling onConfirm
 *   - Two-column layout on desktop (fields | evidence panel)
 */

import { useState, useCallback } from 'react'
import { ev, isWeak } from '../utils.js'
import { patchExtraction } from '../api.js'
import ErrorState from './ErrorState.jsx'
import Disclaimer from './Disclaimer.jsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getEvidenceField(obj) {
  if (!obj) return null
  if (typeof obj === 'object' && 'value' in obj) return obj
  return null
}

// ─── Editable field ───────────────────────────────────────────────────────────

function EditableField({
  label,
  fieldKey,      // dot-path key used for dirty tracking
  value,         // current display value (already extracted)
  evidence,      // EvidenceField envelope — for page/quote
  unit = '',
  type = 'text', // 'text' | 'number'
  weak,
  edited,
  onFocus,       // () => void — tell parent which field is focused
  onChange,      // (key, newValue) => void
}) {
  const [local, setLocal] = useState(value ?? '')

  function handleChange(e) {
    const raw = e.target.value
    setLocal(raw)
    const parsed = type === 'number' ? (raw === '' ? null : Number(raw)) : raw
    onChange(fieldKey, parsed)
  }

  const isEdited = edited[fieldKey] !== undefined

  return (
    <div className="w-full flex flex-col gap-0.5 py-2 border-b border-border last:border-0">
      <div className="flex items-center gap-1.5 min-w-0">
        <label htmlFor={fieldKey} className="text-xs text-muted flex-1 min-w-0 truncate">{label}</label>
        {isEdited && (
          <span className="shrink-0 text-xs font-medium px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 border border-blue-200 whitespace-nowrap">
            edited by you
          </span>
        )}
        {weak && !isEdited && (
          <span className="shrink-0 text-xs text-amber-600 whitespace-nowrap">⚠ low confidence</span>
        )}
      </div>
      <div className="flex items-center gap-2 min-w-0">
        <input
          id={fieldKey}
          type={type === 'number' ? 'number' : 'text'}
          step={type === 'number' ? 'any' : undefined}
          value={local}
          onFocus={() => onFocus(evidence)}
          onChange={handleChange}
          className={`flex-1 min-w-0 w-full rounded-lg border px-3 py-1.5 text-sm font-medium focus:outline-none
            focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1
            ${isEdited ? 'border-blue-300 bg-blue-50' :
              weak ? 'border-amber-300 bg-amber-50 text-amber-800' :
              'border-border bg-white text-gray-900'
            }`}
          aria-label={label}
        />
        {unit && <span className="shrink-0 text-xs text-muted">{unit}</span>}
      </div>
    </div>
  )
}

// ─── Charge editable row ──────────────────────────────────────────────────────

function ChargeRow({ charge, idx, edited, onChange, onFocus }) {
  const key = `charges.${idx}.amount_inr`
  const isEdited = edited[key] !== undefined
  const [local, setLocal] = useState(charge.amount_inr ?? '')

  function handleChange(e) {
    const raw = e.target.value
    setLocal(raw)
    onChange(key, raw === '' ? null : Number(raw))
  }

  const evidenceForCharge = charge.page != null || charge.quote ? {
    page: charge.page, quote: charge.quote
  } : null

  return (
    <div className="flex items-center gap-3 py-2 border-b border-border last:border-0 text-sm">
      <div className="flex-1 min-w-0">
        <span className="font-medium text-gray-900 block truncate">{charge.name || '—'}</span>
        <span className="text-xs text-muted">{charge.category || ''} · {charge.payee || 'lender'}</span>
        {charge.frequency && charge.frequency !== 'one_time' && (
          <span className="block text-xs text-amber-700">{charge.frequency.replace('_', ' ')}</span>
        )}
      </div>

      <div className="shrink-0 flex flex-col items-end gap-0.5">
        <div className="flex items-center gap-1">
          <span className="text-xs text-muted shrink-0">₹</span>
          <input
            type="number"
            step="any"
            value={local}
            onFocus={() => onFocus(evidenceForCharge)}
            onChange={handleChange}
            className={`w-28 rounded border px-2 py-1 text-sm text-right font-medium focus:outline-none
              focus-visible:ring-1 focus-visible:ring-brand-500
              ${isEdited ? 'border-blue-300 bg-blue-50' : 'border-border bg-white'}`}
            aria-label={`Amount for ${charge.name}`}
          />
        </div>
        {isEdited && (
          <span className="text-xs text-blue-600 whitespace-nowrap">edited</span>
        )}
      </div>
    </div>
  )
}

// ─── Section heading ──────────────────────────────────────────────────────────

function SectionHeading({ children }) {
  return (
    <h2 className="text-sm font-semibold text-muted uppercase tracking-wide mb-2 mt-5 first:mt-0">
      {children}
    </h2>
  )
}

// ─── Evidence panel ───────────────────────────────────────────────────────────

function EvidencePanel({ evidence }) {
  if (!evidence) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-surface p-4 text-sm text-muted text-center">
        <p className="text-lg mb-1" aria-hidden="true">🔍</p>
        <p>Click any field to see the source text from the document.</p>
      </div>
    )
  }

  const { page, quote, confidence, status } = evidence

  return (
    <div className="rounded-xl border border-brand-100 bg-brand-50 p-4 space-y-3">
      <p className="text-xs font-semibold text-brand-700 uppercase tracking-wide">Source evidence</p>

      {page != null && (
        <div>
          <span className="text-xs text-muted">Page</span>
          <p className="font-medium text-gray-900 text-sm">{page}</p>
        </div>
      )}

      {quote && (
        <div>
          <span className="text-xs text-muted">Verbatim quote</span>
          <blockquote className="mt-0.5 pl-2 border-l-2 border-brand-400 text-sm text-gray-800 italic">
            "{quote}"
          </blockquote>
        </div>
      )}

      {confidence != null && (
        <div>
          <span className="text-xs text-muted">Confidence</span>
          <div className="flex items-center gap-2 mt-0.5">
            <div className="flex-1 h-1.5 rounded-full bg-brand-100 overflow-hidden">
              <div
                className={`h-full rounded-full ${confidence >= 0.8 ? 'bg-green-500' : confidence >= 0.6 ? 'bg-amber-500' : 'bg-red-500'}`}
                style={{ width: `${confidence * 100}%` }}
              />
            </div>
            <span className="text-xs font-medium text-gray-700">{Math.round(confidence * 100)}%</span>
          </div>
        </div>
      )}

      {status === 'not_found' && (
        <p className="text-xs text-amber-700">⚠ Not found in the document — value may be inaccurate.</p>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function Review({ extraction: extractionRecord, onConfirm, onBack }) {
  const [busy,    setBusy]    = useState(false)
  const [error,   setError]   = useState(null)
  const [edited,  setEdited]  = useState({})     // { fieldKey: newValue }
  const [focused, setFocused] = useState(null)   // evidence object for the right panel

  if (!extractionRecord) {
    return <ErrorState title="No extraction data" message="Please re-upload your document." onRetry={onBack} />
  }

  const { extraction_id, extraction: ext } = extractionRecord

  const loan      = ext?.loan      ?? {}
  const rate      = ext?.rate      ?? {}
  const lender    = ext?.lender    ?? {}
  const charges   = ext?.charges   ?? []
  const statedApr = ext?.stated_apr_pct  // EvidenceField

  // Weak banner
  const hasWeak =
    isWeak(loan.sanctioned_amount_inr) ||
    isWeak(rate.interest_rate_pct) ||
    isWeak(loan.term_months)

  const dirtyCount = Object.keys(edited).length

  // ── onChange ──
  const handleChange = useCallback((key, value) => {
    setEdited(prev => ({ ...prev, [key]: value }))
  }, [])

  // ── onFocus — show evidence in right panel ──
  const handleFocus = useCallback((evidence) => {
    setFocused(evidence)
  }, [])

  // ── Confirm: PATCH dirty fields then call onConfirm ──
  async function handleProceed() {
    setError(null)
    setBusy(true)
    try {
      if (dirtyCount > 0) {
        // Build flat patch object — PATCH /api/v1/extractions/{id} expects { fields: {...} }
        const fields = {}

        // Map dot-path keys back to the extraction structure
        for (const [key, value] of Object.entries(edited)) {
          if (key.startsWith('charges.')) {
            // charges.{idx}.amount_inr
            const parts = key.split('.')
            const idx = parseInt(parts[1], 10)
            if (!fields.charges) {
              // Send the full charges array with the edit applied
              fields.charges = charges.map((c, i) => {
                if (i === idx) return { ...c, amount_inr: edited[`charges.${i}.amount_inr`] ?? c.amount_inr }
                return c
              })
            }
          } else {
            // Simple nested path: loan.term_months → loan: { term_months: { value: X } }
            const [section, ...rest] = key.split('.')
            if (!fields[section]) fields[section] = {}
            // Wrap EvidenceField values back in the envelope
            const fieldName = rest[0]
            fields[section][fieldName] = { value, status: 'user_edited' }
          }
        }

        await patchExtraction(extraction_id, fields)
      }

      await onConfirm(extraction_id)
    } catch (e) {
      setError(e.message || 'Failed. Please try again.')
      setBusy(false)
    }
  }

  return (
    <main className="w-full max-w-5xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6 space-y-1">
        <h1 className="text-2xl font-bold text-gray-900">Review extracted data</h1>
        <p className="text-sm text-muted">
          Check every field and correct any errors. Click a field to see the source quote.
          Changes are saved before analysis.
        </p>
      </div>

      {/* Weak-fields banner */}
      {hasWeak && (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 flex gap-2"
        >
          <span aria-hidden="true">⚠</span>
          <span>Some critical fields were not found or have low confidence. Results may be less accurate.</span>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="mb-4">
          <ErrorState title="Could not proceed" message={error} onRetry={handleProceed} />
        </div>
      )}

      {/* Two-column layout: single col mobile, grid on lg+ */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)] gap-6">

        {/* Left: editable fields */}
        <div className="min-w-0">

          {/* ── Loan ── */}
          <section>
            <SectionHeading>Loan</SectionHeading>
            <div className="w-full rounded-xl border border-border bg-white px-4">
              <EditableField
                label="Sanctioned amount (₹)"
                fieldKey="loan.sanctioned_amount_inr"
                value={ev(loan.sanctioned_amount_inr)}
                evidence={getEvidenceField(loan.sanctioned_amount_inr)}
                type="number"
                weak={isWeak(loan.sanctioned_amount_inr)}
                edited={edited}
                onFocus={handleFocus}
                onChange={handleChange}
              />
              <EditableField
                label="Loan term"
                fieldKey="loan.term_months"
                value={ev(loan.term_months)}
                evidence={getEvidenceField(loan.term_months)}
                unit="months"
                type="number"
                weak={isWeak(loan.term_months)}
                edited={edited}
                onFocus={handleFocus}
                onChange={handleChange}
              />
              {(loan.instalments ?? []).length > 0 && (
                <EditableField
                  label={`${loan.instalments[0].type ?? 'Instalment'} amount (₹)`}
                  fieldKey="loan.instalments.0.amount_inr"
                  value={ev(loan.instalments[0].amount_inr)}
                  evidence={getEvidenceField(loan.instalments[0].amount_inr)}
                  type="number"
                  weak={isWeak(loan.instalments[0].amount_inr)}
                  edited={edited}
                  onFocus={handleFocus}
                  onChange={handleChange}
                />
              )}
            </div>
          </section>

          {/* ── Rate ── */}
          <section>
            <SectionHeading>Interest rate</SectionHeading>
            <div className="w-full rounded-xl border border-border bg-white px-4">
              <EditableField
                label="Interest rate"
                fieldKey="rate.interest_rate_pct"
                value={ev(rate.interest_rate_pct)}
                evidence={getEvidenceField(rate.interest_rate_pct)}
                unit="%"
                type="number"
                weak={isWeak(rate.interest_rate_pct)}
                edited={edited}
                onFocus={handleFocus}
                onChange={handleChange}
              />
              {ev(rate.type) && (
                <div className="py-2 border-b border-border last:border-0">
                  <span className="text-xs text-muted block">Rate type</span>
                  <span className="text-sm font-medium text-gray-900">{ev(rate.type)}</span>
                </div>
              )}
            </div>
          </section>

          {/* ── Stated APR ── */}
          {statedApr !== undefined && (
            <section>
              <SectionHeading>Stated APR</SectionHeading>
              <div className="w-full rounded-xl border border-border bg-white px-4">
                <EditableField
                  label="Stated APR"
                  fieldKey="stated_apr_pct"
                  value={ev(statedApr)}
                  evidence={getEvidenceField(statedApr)}
                  unit="%"
                  type="number"
                  weak={isWeak(statedApr)}
                  edited={edited}
                  onFocus={handleFocus}
                  onChange={handleChange}
                />
              </div>
            </section>
          )}

          {/* ── Charges ── */}
          <section>
            <SectionHeading>
              Charges {charges.length > 0 ? `(${charges.length})` : ''}
            </SectionHeading>
            {charges.length > 0 ? (
              <div className="w-full rounded-xl border border-border bg-white px-4">
                {charges.map((c, i) => (
                  <ChargeRow
                    key={i}
                    charge={c}
                    idx={i}
                    edited={edited}
                    onChange={handleChange}
                    onFocus={handleFocus}
                  />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted italic">No charges found in the document.</p>
            )}
          </section>

          {/* ── Actions ── */}
          <div className="flex flex-col sm:flex-row gap-3 pt-6">
            <button
              onClick={handleProceed}
              disabled={busy}
              className="flex-1 min-h-[3rem] px-6 py-3 rounded-xl bg-brand-600 hover:bg-brand-700 disabled:opacity-60
                         text-white font-semibold text-base whitespace-nowrap focus-ring transition-colors"
            >
              {busy
                ? 'Saving & analysing…'
                : dirtyCount > 0
                  ? `Save ${dirtyCount} edit${dirtyCount !== 1 ? 's' : ''} & Analyse`
                  : 'Looks good — Analyse'}
            </button>
            <button
              onClick={onBack}
              disabled={busy}
              className="flex-1 min-h-[3rem] px-6 py-3 rounded-xl border border-border hover:bg-surface
                         text-gray-700 font-medium text-base whitespace-nowrap focus-ring transition-colors"
            >
              ← Try another file
            </button>
          </div>
        </div>

        {/* Right: evidence panel — below fields on mobile, sticky column on lg+ */}
        <div className="min-w-0">
          <div className="lg:sticky lg:top-6 space-y-4">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">Document evidence</p>
            <EvidencePanel evidence={focused} />

            {/* Lender info — read-only */}
            {lender.name && (
              <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm">
                <p className="text-xs text-muted mb-1">Lender</p>
                <p className="font-medium text-gray-900">{lender.name}</p>
                {lender.type && <p className="text-xs text-muted capitalize">{lender.type}</p>}
              </div>
            )}
          </div>
        </div>

      </div>

      <Disclaimer />
    </main>
  )
}
