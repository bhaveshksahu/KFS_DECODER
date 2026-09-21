/** Error / empty state card — used across all screens. */
export default function ErrorState({ title, message, onRetry, onManual }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-300 bg-red-50 p-6 text-center space-y-3"
    >
      <p className="text-2xl" aria-hidden="true">⚠</p>
      <h2 className="font-semibold text-red-800 text-lg">{title}</h2>
      {message && <p className="text-red-700 text-sm">{message}</p>}
      <div className="flex flex-col sm:flex-row gap-2 justify-center pt-2">
        {onRetry && (
          <button
            onClick={onRetry}
            className="px-5 py-2 rounded-lg bg-red-700 text-white text-sm font-medium hover:bg-red-800 focus-ring"
          >
            Try again
          </button>
        )}
        {onManual && (
          <button
            onClick={onManual}
            className="px-5 py-2 rounded-lg border border-red-300 text-red-700 text-sm font-medium hover:bg-red-100 focus-ring"
          >
            Enter values manually
          </button>
        )}
      </div>
    </div>
  )
}
