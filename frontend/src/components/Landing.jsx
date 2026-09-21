import { useState, useEffect, useRef } from 'react'
import { listSamples, loadSample } from '../api.js'
import { IS_DEMO } from '../utils.js'
import Disclaimer from './Disclaimer.jsx'
import ErrorState from './ErrorState.jsx'

const ACCEPT = '.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg'
const MAX_MB  = 10

export default function Landing({ onSampleLoaded, onUploadStart }) {
  const [samples,   setSamples]   = useState([])
  const [loadingKey, setLoadingKey] = useState(null)
  const [dragging,  setDragging]  = useState(false)
  const [error,     setError]     = useState(null)
  const inputRef = useRef(null)

  useEffect(() => {
    listSamples()
      .then(d => setSamples((d.samples || []).filter(s => s.has_data)))
      .catch(() => {})
  }, [])

  // Demo mode: auto-load the synthetic sample immediately
  useEffect(() => {
    if (IS_DEMO) handleSample('synthetic_bad_kfs')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSample(key) {
    setError(null)
    setLoadingKey(key)
    try {
      const data = await loadSample(key)
      onSampleLoaded(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingKey(null)
    }
  }

  function validateAndUpload(file) {
    if (!file) return
    const mb = file.size / 1024 / 1024
    if (mb > MAX_MB) {
      setError(`File is ${mb.toFixed(1)} MB. Maximum allowed is ${MAX_MB} MB.`)
      return
    }
    const ok = ['application/pdf', 'image/png', 'image/jpeg'].includes(file.type)
    if (!ok) {
      setError('Only PDF, PNG, and JPG files are supported.')
      return
    }
    setError(null)
    onUploadStart(file)
  }

  function onFileChange(e) {
    validateAndUpload(e.target.files?.[0])
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    validateAndUpload(e.dataTransfer.files?.[0])
  }

  return (
    <main className="max-w-lg mx-auto w-full flex flex-col items-center px-4 py-10 space-y-10">
      {/* Headline */}
      <div className="text-center space-y-2 max-w-xl">
        <h1 className="text-3xl sm:text-4xl font-bold text-gray-900 leading-tight">
          Is your loan really that cheap?
        </h1>
        <p className="text-muted text-base sm:text-lg">
          Upload your Key Facts Statement (KFS) and we'll show you the real annual cost —
          in seconds, with every charge accounted for.
        </p>
      </div>

      {/* Upload zone */}
      <div
        className={`w-full max-w-lg rounded-2xl border-2 border-dashed p-8 text-center cursor-pointer transition-colors
          ${dragging ? 'border-brand-500 bg-brand-50' : 'border-border hover:border-brand-500 hover:bg-surface'}`}
        role="button"
        tabIndex={0}
        aria-label="Upload your KFS document"
        onClick={() => inputRef.current?.click()}
        onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div className="text-4xl mb-3 pointer-events-none" aria-hidden="true">📄</div>
        <p className="font-medium text-gray-800 pointer-events-none">
          Drag and drop your KFS here
        </p>
        <p className="text-sm text-muted mt-1 pointer-events-none">
          PDF, PNG, or JPG · up to {MAX_MB} MB
        </p>
        <button
          type="button"
          className="mt-4 px-6 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg font-medium text-sm focus-ring"
          onClick={e => { e.stopPropagation(); inputRef.current?.click() }}
        >
          Choose file
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          aria-hidden="true"
          onChange={onFileChange}
        />
      </div>

      {error && (
        <ErrorState
          title="Cannot process this file"
          message={error}
          onRetry={() => { setError(null); inputRef.current?.click() }}
        />
      )}

      {/* Samples */}
      {samples.length > 0 && (
        <section aria-labelledby="samples-heading" className="w-full max-w-lg">
          <h2 id="samples-heading" className="text-sm font-semibold text-muted uppercase tracking-wide mb-3">
            Or try a sample
          </h2>
          <div className="flex flex-col gap-2">
            {samples.map(s => (
              <button
                key={s.key}
                onClick={() => handleSample(s.key)}
                disabled={loadingKey != null}
                aria-busy={loadingKey === s.key}
                className="flex items-start gap-3 w-full text-left px-4 py-3 rounded-xl border border-border
                           hover:border-brand-500 hover:bg-surface disabled:opacity-50 focus-ring transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <span className="font-medium text-gray-900 block">{s.label}</span>
                  <span className="text-xs text-muted line-clamp-2">{s.description}</span>
                </div>
                {s.synthetic && (
                  <span className="shrink-0 mt-0.5 text-xs font-medium px-2 py-0.5 rounded-full
                                   bg-amber-100 text-amber-800 border border-amber-300">
                    Synthetic
                  </span>
                )}
                {loadingKey === s.key && (
                  <span className="shrink-0 text-brand-600 text-sm" aria-label="Loading">⏳</span>
                )}
              </button>
            ))}
          </div>
        </section>
      )}

      <Disclaimer />
    </main>
  )
}
