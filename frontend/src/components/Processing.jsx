/**
 * Processing screen — shown while upload + extract + analyse is in flight.
 * Props:
 *   stage: 'uploading' | 'extracting' | 'analysing'
 *   fileName: string | null
 */

import Disclaimer from './Disclaimer.jsx'

const STAGES = {
  uploading:  { step: 1, label: 'Uploading document…',   hint: 'Sending your file securely.' },
  extracting: { step: 2, label: 'Reading the KFS…',      hint: 'Gemini is extracting loan fields from your document.' },
  analysing:  { step: 3, label: 'Computing APR…',        hint: 'Running the deterministic rules engine.' },
}
const TOTAL = 3

export default function Processing({ stage = 'uploading', fileName }) {
  const { step, label, hint } = STAGES[stage] ?? STAGES.uploading
  const pct = Math.round((step / TOTAL) * 100)

  return (
    <main
      className="max-w-lg mx-auto w-full flex flex-col items-center justify-center min-h-[60vh] px-4 py-12 space-y-8"
      aria-live="polite"
      aria-busy="true"
    >
      {/* Spinner */}
      <div
        className="w-16 h-16 rounded-full border-4 border-brand-100 border-t-brand-600 animate-spin"
        role="img"
        aria-label="Loading"
      />

      {/* Stage label */}
      <div className="text-center space-y-1">
        <p className="text-xl font-semibold text-gray-900">{label}</p>
        {fileName && (
          <p className="text-sm text-muted truncate max-w-xs" title={fileName}>
            {fileName}
          </p>
        )}
        <p className="text-sm text-muted">{hint}</p>
      </div>

      {/* Progress bar */}
      <div
        className="w-full max-w-sm"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${pct}% complete`}
      >
        <div className="flex justify-between text-xs text-muted mb-1">
          <span>Step {step} of {TOTAL}</span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 rounded-full bg-brand-100 overflow-hidden">
          <div
            className="h-full rounded-full bg-brand-600 transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <p className="text-xs text-muted text-center">
        This usually takes 10–20 seconds.
      </p>

      <Disclaimer />
    </main>
  )
}
