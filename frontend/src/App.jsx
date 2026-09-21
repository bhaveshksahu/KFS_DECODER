/**
 * App.jsx — top-level state machine for the KFS Decoder.
 *
 * States:
 *   landing      → user uploads or picks a sample
 *   processing   → upload → extract → analyse pipeline in flight
 *   review       → show extracted fields, user confirms
 *   results      → show full analysis output
 *   error        → unrecoverable error with retry
 *
 * Sample normalisation:
 *   The /samples/{key}/load response stores a *flat* analysis blob (legacy shape).
 *   The live /extractions/{id}/analyze response returns a *nested* shape with
 *   apr:{...} and summary:{...} sub-objects.
 *   We normalise the flat shape to nested here so Results only sees one format.
 */

import { useState, useCallback } from 'react'
import Landing    from './components/Landing.jsx'
import Processing from './components/Processing.jsx'
import Review     from './components/Review.jsx'
import Results    from './components/Results.jsx'
import ErrorState from './components/ErrorState.jsx'
import { uploadDocument, extractDocument, analyzeExtraction } from './api.js'

// ─── Normalise flat sample analysis → nested live shape ───────────────────────
//
// Flat (sample JSON):
//   { apr_interest_only_pct, apr_all_charges_pct, apr_excl_third_party_pct,
//     effective_annual_all_charges_pct, instalment_used, n_periods,
//     total_repayable, total_interest, total_charges_inr, fee_impact_pp,
//     charge_pct_of_loan, stamp_duty_included_in_apr, stamp_duty_assumption,
//     rule_results: [{rule_id, status, severity, plain_text_en}] }
//
// Nested (live API):
//   { apr: {interest_only_pct, all_charges_pct, excluding_third_party_pct,
//           effective_annual_all_charges_pct, stamp_duty_included, instalment_used, n_periods},
//     summary: {total_repayable, total_interest, total_charges_inr, fee_impact_pp,
//               charge_pct_of_loan, stamp_duty_assumption, charge_breakdown:[]},
//     rule_results: [{rule_id, status, severity, evidence, citation, plain_text_en, plain_text_hi}],
//     in_scope, analysis_id, extraction_id }

function normaliseAnalysis(raw) {
  if (!raw) return null

  // Already in nested shape (live API)
  if (raw.apr !== undefined || raw.summary !== undefined) return raw

  // Flat shape — convert
  const apr = {
    interest_only_pct:               raw.apr_interest_only_pct ?? null,
    all_charges_pct:                 raw.apr_all_charges_pct ?? null,
    excluding_third_party_pct:       raw.apr_excl_third_party_pct ?? null,
    effective_annual_all_charges_pct:raw.effective_annual_all_charges_pct ?? null,
    stamp_duty_included:             raw.stamp_duty_included_in_apr ?? false,
    instalment_used:                 raw.instalment_used ?? null,
    n_periods:                       raw.n_periods ?? null,
  }

  const summary = {
    total_repayable:      raw.total_repayable ?? null,
    total_interest:       raw.total_interest ?? null,
    total_charges_inr:    raw.total_charges_inr ?? null,
    fee_impact_pp:        raw.fee_impact_pp ?? null,
    charge_pct_of_loan:   raw.charge_pct_of_loan ?? null,
    stamp_duty_assumption:raw.stamp_duty_assumption ?? '',
    charge_breakdown:     raw.charge_breakdown ?? [],
  }

  return {
    analysis_id:    raw.analysis_id ?? null,
    extraction_id:  raw.extraction_id ?? null,
    in_scope:       raw.in_scope ?? true,
    apr,
    summary,
    rule_results:   raw.rule_results ?? [],
  }
}

// ─── Shared layout shell ──────────────────────────────────────────────────────

function Shell({ children }) {
  return (
    <div className="min-h-screen bg-white">
      {/* Nav bar */}
      <header className="border-b border-border px-4 py-3 flex items-center gap-3">
        <span className="font-bold text-gray-900 text-base leading-none">KFS Decoder</span>
        <span className="text-xs text-muted leading-none hidden sm:block">
          Know the real cost of your loan
        </span>
      </header>
      {/* Each screen owns its own max-width via its <main> element */}
      {children}
    </div>
  )
}

// ─── State constants ──────────────────────────────────────────────────────────

const STATE = {
  LANDING:    'landing',
  PROCESSING: 'processing',
  REVIEW:     'review',
  RESULTS:    'results',
  ERROR:      'error',
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [screen,     setScreen]     = useState(STATE.LANDING)
  const [stage,      setStage]      = useState('uploading')
  const [fileName,   setFileName]   = useState(null)
  const [extraction, setExtraction] = useState(null)  // full extract API response
  const [analysis,   setAnalysis]   = useState(null)  // normalised analysis
  const [error,      setError]      = useState(null)

  // ── Reset to landing ──
  const reset = useCallback(() => {
    setScreen(STATE.LANDING)
    setStage('uploading')
    setFileName(null)
    setExtraction(null)
    setAnalysis(null)
    setError(null)
  }, [])

  // ── Sample loaded — flat analysis; also keep extraction for amortisation ──
  const handleSampleLoaded = useCallback((data) => {
    setExtraction(data.extraction ? { extraction_id: data.extraction_id, extraction: data.extraction } : null)
    setAnalysis(normaliseAnalysis(data.analysis))
    setScreen(STATE.RESULTS)
  }, [])

  // ── User picked a file → upload → extract → show Review ──
  const handleUploadStart = useCallback(async (file) => {
    setFileName(file.name)
    setError(null)
    setScreen(STATE.PROCESSING)
    setStage('uploading')

    try {
      const { doc_id } = await uploadDocument(file)

      setStage('extracting')
      const extractResult = await extractDocument(doc_id)

      if (!extractResult.in_scope) {
        setAnalysis({
          in_scope: false,
          reason: extractResult.reason,
          rule_results: [],
          apr: null,
          summary: null,
        })
        setScreen(STATE.RESULTS)
        return
      }

      setExtraction(extractResult)
      setScreen(STATE.REVIEW)
    } catch (e) {
      setError(e.message || 'Something went wrong. Please try again.')
      setScreen(STATE.ERROR)
    }
  }, [])

  // ── User confirmed Review → run analysis ──
  const handleConfirmReview = useCallback(async (extractionId) => {
    setStage('analysing')
    setScreen(STATE.PROCESSING)

    const result = await analyzeExtraction(extractionId)
    setAnalysis(normaliseAnalysis(result))
    setScreen(STATE.RESULTS)
    // Errors bubble up to Review.jsx which shows inline error and stays on review
  }, [])

  // ── Render ──
  switch (screen) {
    case STATE.LANDING:
      return (
        <Shell>
          <Landing
            onSampleLoaded={handleSampleLoaded}
            onUploadStart={handleUploadStart}
          />
        </Shell>
      )

    case STATE.PROCESSING:
      return (
        <Shell>
          <Processing stage={stage} fileName={fileName} />
        </Shell>
      )

    case STATE.REVIEW:
      return (
        <Shell>
          <Review
            extraction={extraction}
            onConfirm={handleConfirmReview}
            onBack={reset}
          />
        </Shell>
      )

    case STATE.RESULTS:
      return (
        <Shell>
          <Results
            analysis={analysis}
            extraction={extraction}
            onReset={reset}
          />
        </Shell>
      )

    case STATE.ERROR:
    default:
      return (
        <Shell>
          <div className="max-w-lg mx-auto px-4 py-12">
            <ErrorState
              title="Something went wrong"
              message={error}
              onRetry={reset}
            />
          </div>
        </Shell>
      )
  }
}
