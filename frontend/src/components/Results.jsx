/**
 * Results screen — full APR + rules analysis output.
 *
 * Props:
 *   analysis   : normalised analysis object (nested shape)
 *   extraction : { extraction_id, extraction: KFSExtraction } | null
 *   onReset    : () => void
 */

import { useState, useMemo } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import {
  fmtINR, fmtPct, fmtPP,
  STATUS_LABEL,
  sortRules,
  ev,
} from '../utils.js'
import Disclaimer from './Disclaimer.jsx'

// ─── Colour helpers ───────────────────────────────────────────────────────────

const PAYEE_COLOR = { lender: '#2563eb', third_party: '#7c3aed', stamp_duty_excluded: '#d1d5db' }

// Icons keyed by STATUS, not severity (per spec)
const STATUS_ICON = {
  fail:           '✗',
  warn:           '⚠',
  not_stated:     '?',
  pass:           '✓',
  not_applicable: '—',
}

// ─── Tiny shared components ───────────────────────────────────────────────────

function Card({ children, className = '' }) {
  return (
    <div className={`rounded-2xl border border-border bg-white p-5 ${className}`}>
      {children}
    </div>
  )
}

function SectionHeading({ children, id }) {
  return (
    <h2 id={id} className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
      {children}
    </h2>
  )
}

function Metric({ label, value, sub, highlight }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted uppercase tracking-wide">{label}</span>
      <span className={`text-2xl font-bold ${highlight ? 'text-brand-600' : 'text-gray-900'}`}>
        {value ?? '—'}
      </span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  )
}

// ─── 1. Hero card ─────────────────────────────────────────────────────────────
//
// "Stated APR X% → Real APR Y%"  delta with icon + label
// "You will pay ₹A in total; ₹B is charges"

function HeroCard({ apr, summary, statedApr }) {
  if (!apr) return null

  const stated    = statedApr != null ? Number(statedApr) : null
  const realApr   = apr.all_charges_pct
  const delta     = stated != null && realApr != null ? realApr - stated : null
  const totalRep  = summary?.total_repayable
  const totalChg  = summary?.total_charges_inr

  const deltaColour =
    delta == null ? 'text-gray-500' :
    delta > 2     ? 'text-red-700' :
    delta > 0.5   ? 'text-amber-700' :
    'text-green-700'

  const deltaIcon =
    delta == null ? null :
    delta > 2     ? '⬆' :
    delta > 0.5   ? '↑' :
    '≈'

  const deltaLabel =
    delta == null ? null :
    delta > 2     ? 'significantly higher than stated' :
    delta > 0.5   ? 'higher than stated' :
    'matches stated'

  return (
    <Card className="bg-surface">
      {/* APR comparison */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-5">
        {stated != null && (
          <>
            <div className="text-center sm:text-left">
              <p className="text-xs text-muted uppercase tracking-wide">Stated APR</p>
              <p className="text-3xl font-bold text-gray-500">{fmtPct(stated)}</p>
            </div>

            <div className={`text-2xl font-bold self-center ${deltaColour}`} aria-hidden="true">→</div>
          </>
        )}

        <div className="text-center sm:text-left">
          <p className="text-xs text-muted uppercase tracking-wide">Real APR (all charges)</p>
          <p className={`text-3xl font-bold ${delta != null && delta > 0.5 ? 'text-red-700' : 'text-gray-900'}`}>
            {fmtPct(realApr)}
          </p>
        </div>

        {delta != null && (
          <div className={`flex items-center gap-1.5 sm:ml-auto text-sm font-semibold ${deltaColour}`}>
            <span aria-hidden="true">{deltaIcon}</span>
            <span className="sr-only">Delta: </span>
            <span>{fmtPP(delta)}</span>
          </div>
        )}
      </div>

      {delta != null && deltaLabel && (
        <p className={`mt-1 text-xs font-medium ${deltaColour}`}>{deltaLabel}</p>
      )}

      {/* Total repayable / charges row */}
      {(totalRep != null || totalChg != null) && (
        <p className="mt-4 text-sm text-gray-700 border-t border-border pt-3">
          You will pay{' '}
          <strong className="text-gray-900">{fmtINR(totalRep)}</strong> in total
          {totalChg != null && totalChg > 0 && (
            <>; <strong className="text-red-700">{fmtINR(totalChg)}</strong> is charges</>
          )}
          .
        </p>
      )}
    </Card>
  )
}

// ─── 2. APR detail table ─────────────────────────────────────────────────────

function APRCard({ apr }) {
  if (!apr) return null

  const rows = [
    { label: 'Interest only (baseline)',  value: fmtPct(apr.interest_only_pct) },
    { label: 'All charges (real APR)',    value: fmtPct(apr.all_charges_pct),                     bold: true },
    { label: 'Excl. third-party fees',   value: fmtPct(apr.excluding_third_party_pct) },
    { label: 'Effective annual rate',     value: fmtPct(apr.effective_annual_all_charges_pct),    muted: true },
  ]

  return (
    <Card>
      <SectionHeading>APR breakdown</SectionHeading>
      <div className="divide-y divide-border">
        {rows.map(r => (
          <div key={r.label} className="flex justify-between items-center py-2.5 text-sm">
            <span className={r.muted ? 'text-muted' : 'text-gray-700'}>{r.label}</span>
            <span className={r.bold ? 'font-bold text-brand-600 text-base' : 'font-medium text-gray-900'}>
              {r.value}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted">
        Based on {apr.n_periods} monthly instalments of {fmtINR(apr.instalment_used)}.
        {apr.stamp_duty_included ? ' Stamp duty included.' : ''}
      </p>
    </Card>
  )
}

// ─── 3. Summary metrics ───────────────────────────────────────────────────────

function SummaryCard({ summary }) {
  if (!summary) return null
  return (
    <Card>
      <SectionHeading>Loan summary</SectionHeading>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        <Metric label="Total repayable"  value={fmtINR(summary.total_repayable)} highlight />
        <Metric label="Total interest"   value={fmtINR(summary.total_interest)} />
        <Metric label="Total charges"    value={fmtINR(summary.total_charges_inr)} />
        <Metric
          label="Fee impact"
          value={fmtPP(summary.fee_impact_pp)}
          sub="percentage points added by fees"
        />
      </div>
      {summary.stamp_duty_assumption && (
        <p className="mt-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          {summary.stamp_duty_assumption}
        </p>
      )}
    </Card>
  )
}

// ─── 4. Charge bar chart ─────────────────────────────────────────────────────

function ChargeChart({ breakdown }) {
  if (!breakdown || breakdown.length === 0) return null

  const data = breakdown
    .filter(b => b.total_inr > 0)
    .sort((a, b) => b.total_inr - a.total_inr)
    .map(b => ({
      name: (b.names?.[0] ?? b.category).slice(0, 24),
      amount: b.total_inr,
      payee: b.payee,
    }))

  if (data.length === 0) return null

  return (
    <Card>
      <SectionHeading>Charge breakdown</SectionHeading>
      <div style={{ height: Math.max(140, data.length * 36) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 0, bottom: 0 }}>
            <XAxis
              type="number"
              tickFormatter={v => fmtINR(v, 0)}
              tick={{ fontSize: 11, fill: '#57606a' }}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={120}
              tick={{ fontSize: 11, fill: '#374151' }}
            />
            <Tooltip
              formatter={val => [fmtINR(val), 'Amount']}
              contentStyle={{ fontSize: 12 }}
            />
            <Bar dataKey="amount" radius={[0, 4, 4, 0]}>
              {data.map((d, i) => (
                <Cell key={i} fill={PAYEE_COLOR[d.payee] ?? '#3b82f6'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="flex gap-4 mt-3 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-brand-600" />
          Lender charges
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-purple-600" />
          Third-party charges
        </span>
      </div>
    </Card>
  )
}

// ─── 5. Amortisation table ────────────────────────────────────────────────────

function AmortisationTable({ rows }) {
  const [open, setOpen] = useState(false)
  if (!rows || rows.length === 0) return null

  return (
    <Card>
      {/* Toggle header — plain div + button; no heading element inside button (invalid HTML) */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-muted uppercase tracking-wide">
          Amortisation schedule ({rows.length} periods)
        </h2>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className="shrink-0 ml-3 text-sm text-muted hover:text-gray-700 focus-ring px-1"
          aria-expanded={open}
          aria-label={open ? 'Collapse amortisation schedule' : 'Expand amortisation schedule'}
        >
          {open ? '▲' : '▼'}
        </button>
      </div>

      {open && (
        <div className="mt-4 overflow-x-auto -mx-5 px-5">
          <table className="w-full text-xs text-right border-collapse">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-1.5 text-left font-medium">Period</th>
                <th className="py-1.5 font-medium">Outstanding (₹)</th>
                <th className="py-1.5 font-medium">Principal (₹)</th>
                <th className="py-1.5 font-medium">Interest (₹)</th>
                <th className="py-1.5 font-medium">Instalment (₹)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.n} className="border-b border-border last:border-0">
                  <td className="py-1.5 text-left text-gray-700">{r.n}</td>
                  <td className="py-1.5">{r.outstanding.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                  <td className="py-1.5">{r.principal.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                  <td className="py-1.5">{r.interest.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                  <td className="py-1.5 font-medium">{r.instalment.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted mt-2">
            {rows.length} row{rows.length !== 1 ? 's' : ''}.
            Computed from the stated principal, rate and EMI using reducing-balance method.
            The final period is adjusted so the closing balance is exactly ₹0.
          </p>
        </div>
      )}
    </Card>
  )
}

// ─── 6. Rules panel ───────────────────────────────────────────────────────────

// Status-based badge (not severity-based)
function StatusBadge({ status }) {
  const s = STATUS_LABEL[status] ?? STATUS_LABEL.not_applicable
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${s.bg} ${s.border} ${s.colour}`}>
      {s.label}
    </span>
  )
}

// A rule row that is expandable
function RuleRow({ rule, lang, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const text = (lang === 'hi' && rule.plain_text_hi) ? rule.plain_text_hi : rule.plain_text_en
  const icon = STATUS_ICON[rule.status] ?? '?'

  // Border/bg based on status
  const cardStyle =
    rule.status === 'fail'       ? 'border-red-300 bg-red-50' :
    rule.status === 'warn'       ? 'border-amber-200 bg-amber-50' :
    rule.status === 'not_stated' ? 'border-gray-300 bg-gray-50' :
    'border-border bg-white'

  const iconStyle =
    rule.status === 'fail'       ? 'text-red-700 font-bold' :
    rule.status === 'warn'       ? 'text-amber-700 font-bold' :
    rule.status === 'not_stated' ? 'text-gray-500' :
    'text-green-700 font-bold'

  // Evidence values — may be missing (sample JSON has no evidence)
  const evidenceEntries = rule.evidence
    ? Object.entries(rule.evidence).filter(([, v]) => v != null && v !== false && v !== '')
    : []

  // Suggested question for the lender — derived from rule_id
  const LENDER_QUESTIONS = {
    'R-01': 'Can you show me the full APR computation sheet including all upfront charges?',
    'R-02': 'Why does the stated APR appear to exclude third-party charges? Is this intentional?',
    'R-04': 'For how many working days is this KFS offer valid?',
    'R-05': 'What is the unique proposal / sanction reference number for this offer?',
    'R-06': 'Can you provide the APR computation sheet and complete amortisation schedule?',
    'R-07': 'What are the exact penal, foreclosure, and switching charges that apply?',
    'R-10': 'How was the EMI calculated? Can you show the reducing-balance workings?',
    'R-12': 'What is the total cost of charges as a percentage of the loan amount?',
    'R-13': 'Does this loan fall under the RBI KFS circular (RBI/2024-25/18)?',
  }
  const question = LENDER_QUESTIONS[rule.rule_id]

  return (
    <div className={`rounded-xl border p-4 ${cardStyle}`}>
      {/* Header row — always visible */}
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-start gap-2 text-left focus-ring"
        aria-expanded={open}
      >
        <span className={`text-base leading-5 shrink-0 ${iconStyle}`} aria-hidden="true">
          {icon}
        </span>
        <span className="flex-1 min-w-0">
          <span className="font-semibold text-sm text-gray-900 mr-2">{rule.rule_id}</span>
          <StatusBadge status={rule.status} />
          {text && (
            <span className="block text-sm text-gray-800 leading-relaxed mt-1">{text}</span>
          )}
        </span>
        <span className="shrink-0 text-muted text-xs mt-0.5" aria-hidden="true">
          {open ? '▲' : '▼'}
        </span>
      </button>

      {/* Expanded body */}
      {open && (
        <div className="mt-3 pl-6 space-y-3 text-sm">

          {/* What we found */}
          {evidenceEntries.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">What we found</p>
              <dl className="space-y-0.5">
                {evidenceEntries.map(([k, v]) => (
                  <div key={k} className="flex gap-2 text-xs">
                    <dt className="text-muted shrink-0 w-40 truncate">{k.replace(/_/g, ' ')}</dt>
                    <dd className="font-medium text-gray-800">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {/* Why it matters */}
          {rule.citation && (
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">RBI Citation</p>
              <p className="text-xs text-gray-700">{rule.citation}</p>
            </div>
          )}

          {/* Suggested question */}
          {question && (
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">
                Ask your lender
              </p>
              <p className="text-xs italic text-gray-700">"{question}"</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function RulesPanel({ rules, lang }) {
  const [passOpen, setPassOpen] = useState(false)
  const sorted = useMemo(() => sortRules(rules ?? []), [rules])

  if (!sorted.length) return null

  const active = sorted.filter(r => r.status !== 'pass' && r.status !== 'not_applicable')
  const passes = sorted.filter(r => r.status === 'pass' || r.status === 'not_applicable')

  const fails  = sorted.filter(r => r.status === 'fail').length
  const warns  = sorted.filter(r => r.status === 'warn').length

  return (
    <section aria-labelledby="rules-heading">
      {/* Summary badges */}
      <div className="flex items-center justify-between mb-3">
        <SectionHeading id="rules-heading">
          Compliance checks ({sorted.length})
        </SectionHeading>
        <div className="flex gap-2 text-xs font-medium mb-4">
          {fails > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-red-100 text-red-700 border border-red-300">
              {fails} inconsistent
            </span>
          )}
          {warns > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-300">
              {warns} review
            </span>
          )}
          {fails === 0 && warns === 0 && (
            <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 border border-green-300">
              All clear
            </span>
          )}
        </div>
      </div>

      <div className="space-y-3">
        {/* Active (fail / warn / not_stated) — expanded by default */}
        {active.map(r => (
          <RuleRow key={r.rule_id} rule={r} lang={lang} defaultOpen={r.status === 'fail' || r.status === 'warn'} />
        ))}

        {/* Passed checks — collapsed group */}
        {passes.length > 0 && (
          <div className="rounded-xl border border-border">
            <button
              type="button"
              onClick={() => setPassOpen(v => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-gray-700
                         hover:bg-surface rounded-xl focus-ring"
              aria-expanded={passOpen}
            >
              <span className="flex items-center gap-2">
                <span className="text-green-700" aria-hidden="true">✓</span>
                Passed checks ({passes.length})
              </span>
              <span className="text-muted" aria-hidden="true">{passOpen ? '▲' : '▼'}</span>
            </button>

            {passOpen && (
              <div className="border-t border-border space-y-2 p-3">
                {passes.map(r => (
                  <RuleRow key={r.rule_id} rule={r} lang={lang} defaultOpen={false} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

// ─── Language toggle ──────────────────────────────────────────────────────────

function LangToggle({ lang, setLang }) {
  return (
    <div
      className="flex rounded-lg border border-border overflow-hidden text-sm"
      role="group"
      aria-label="Language"
    >
      {['en', 'hi'].map(l => (
        <button
          key={l}
          onClick={() => setLang(l)}
          className={`px-3 py-1.5 font-medium focus-ring transition-colors ${
            lang === l
              ? 'bg-brand-600 text-white'
              : 'bg-white text-gray-700 hover:bg-surface'
          }`}
          aria-pressed={lang === l}
        >
          {l === 'en' ? 'EN' : 'हिंदी'}
        </button>
      ))}
    </div>
  )
}

// ─── Out-of-scope banner ─────────────────────────────────────────────────────

function OutOfScopeBanner({ reason }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-4 text-sm text-amber-800 space-y-1"
    >
      <p className="font-semibold">This document may be out of scope for RBI KFS rules.</p>
      {reason && <p>{reason}</p>}
      <p className="text-xs text-amber-700">
        APR calculations and rule checks are shown where possible, but may not be applicable.
      </p>
    </div>
  )
}

// ─── Main Results component ───────────────────────────────────────────────────

export default function Results({ analysis, extraction, onReset }) {
  const [lang, setLang] = useState('en')

  if (!analysis) return null

  const { apr, summary, rule_results, in_scope } = analysis

  // Stated APR — from the extraction record (not the analysis)
  const ext = extraction?.extraction
  const statedApr = ext?.stated_apr_pct ? ev(ext.stated_apr_pct) : null

  // Amortisation schedule — prefer the computed schedule from /analyze;
  // fall back to the one extracted from the document (may be partial).
  const amortRows =
    (analysis.amortisation_schedule?.length > 0)
      ? analysis.amortisation_schedule
      : (ext?.amortisation_schedule ?? [])

  return (
    <main className="max-w-lg mx-auto px-4 py-8 space-y-6">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold text-gray-900">Analysis results</h1>
        <LangToggle lang={lang} setLang={setLang} />
      </div>

      {/* Out-of-scope warning */}
      {!in_scope && (
        <OutOfScopeBanner reason={analysis.reason} />
      )}

      {/* Hero card */}
      <HeroCard apr={apr} summary={summary} statedApr={statedApr} />

      {/* APR detail table */}
      <APRCard apr={apr} />

      {/* Summary metrics */}
      <SummaryCard summary={summary} />

      {/* Charge chart */}
      {summary?.charge_breakdown?.length > 0 && (
        <ChargeChart breakdown={summary.charge_breakdown} />
      )}

      {/* Compliance rules */}
      <RulesPanel rules={rule_results} lang={lang} />

      {/* Amortisation schedule (collapsible) */}
      <AmortisationTable rows={amortRows} />

      {/* Reset */}
      <div className="text-center pt-2">
        <button
          onClick={onReset}
          className="px-6 py-2.5 rounded-xl border border-border hover:bg-surface
                     text-gray-700 font-medium text-sm focus-ring transition-colors"
        >
          ← Analyse another document
        </button>
      </div>

      <Disclaimer />
    </main>
  )
}
