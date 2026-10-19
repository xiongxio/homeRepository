import {
  AlertTriangle,
  Lightbulb,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { useState } from 'react'
import type { ResumeAdvice, ResumeAdviceSuggestion, ResumeRecord } from '../types'

interface ResumeAdvisorProps {
  activeResume: ResumeRecord | null
  advice: ResumeAdvice | null
  busy: boolean
  error: string
  onGenerate: () => void
}

const PRIORITY_META: Record<string, { label: string; className: string }> = {
  high: { label: '优先改', className: 'bg-red-100 text-red-700' },
  medium: { label: '建议改', className: 'bg-amber-100 text-amber-700' },
  low: { label: '可选', className: 'bg-zinc-100 text-zinc-600' },
}

function findResumeExcerpt(rawText: string, target: string): string {
  const text = rawText.replace(/\s+/g, ' ').trim()
  if (!text) return ''
  const cleaned = target.replace(/^项目\s*\d+\s*[：:]\s*/, '').trim()
  const base = cleaned.split(/\s+\/\s+/)[0]?.trim() || cleaned
  const key = base.split(/[与及、，,；;]/)[0]?.trim() || base
  const candidates = [base, key, key.slice(0, 14), key.slice(0, 10)].filter(
    (item) => item.length >= 4,
  )
  for (const candidate of candidates) {
    const index = text.indexOf(candidate)
    if (index < 0) continue
    const headingIndex = text.lastIndexOf('【', index)
    const start =
      headingIndex >= 0 && index - headingIndex < 40
        ? headingIndex
        : Math.max(0, index - 16)
    let end = Math.min(text.length, index + 110)
    const nextHeading = text.indexOf('【', index + 1)
    if (nextHeading > index && nextHeading < end) end = nextHeading
    return `${start > 0 ? '…' : ''}${text.slice(start, end).trim()}${
      end < text.length ? '…' : ''
    }`
  }
  return ''
}

export function ResumeAdvisor({
  activeResume,
  advice,
  busy,
  error,
  onGenerate,
}: ResumeAdvisorProps) {
  const [expanded, setExpanded] = useState<number>(-1)
  const [showAll, setShowAll] = useState(false)

  if (!activeResume) return null

  return (
    <section id="resume-advisor" className="tool-card scroll-mt-6">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <Lightbulb className="size-5 text-emerald-600" aria-hidden="true" />
          简历优化建议
        </h2>
        <button
          type="button"
          className="secondary-btn"
          onClick={onGenerate}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : advice ? (
            <RefreshCw className="size-4" aria-hidden="true" />
          ) : (
            <Lightbulb className="size-4" aria-hidden="true" />
          )}
          {busy ? '正在分析' : advice ? '重新生成' : '生成建议'}
        </button>
      </div>

      <p className="mt-2 text-xs leading-5 text-zinc-500">
        默认只显示问题摘要，点击“详情”查看怎么改、为什么改和简历原文。
      </p>

      {error && (
        <p className="mt-3 flex items-start gap-2 text-sm leading-5 text-red-600">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}

      {advice && (
        <>
          <p className="mt-3 text-xs text-zinc-500">
            依据 {advice.question_count} 道真实面试题
            {advice.weak_topic_count > 0 ? ` · ${advice.weak_topic_count} 个低分知识点` : ''}
            {' · '}
            {new Date(advice.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}
          </p>
          <ul className="mt-3 space-y-2.5">
            {(showAll ? advice.suggestions : advice.suggestions.slice(0, 3)).map(
              (item: ResumeAdviceSuggestion, index: number) => {
                const open = expanded === index
                const meta = PRIORITY_META[item.priority] ?? PRIORITY_META.medium
                const reason = item.evidence.slice(0, 2).join('；')
                const excerpt = findResumeExcerpt(activeResume.raw_text, item.target)
                return (
                  <li key={index} className="rounded-lg border border-zinc-200 bg-zinc-50 p-3">
                    <div className="flex items-start gap-2">
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${meta.className}`}
                      >
                        {meta.label}
                      </span>
                      <span className="min-w-0 flex-1 text-sm font-medium leading-5 text-zinc-800">
                        {item.target}
                      </span>
                      <button
                        type="button"
                        className="shrink-0 text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
                        onClick={() => setExpanded(open ? -1 : index)}
                        aria-expanded={open}
                      >
                        {open ? '收起' : '详情'}
                      </button>
                    </div>

                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-zinc-600">
                      {item.issue}
                    </p>

                    {open && (
                      <div className="mt-2.5 space-y-2 border-t border-zinc-200 pt-2.5 text-xs leading-5">
                        <div>
                          <p className="font-semibold text-zinc-500">现有问题</p>
                          <p className="mt-0.5 text-zinc-700">{item.issue}</p>
                        </div>
                        {item.jd_requirement && (
                          <div>
                            <p className="font-semibold text-zinc-500">对应 JD 要求</p>
                            <p className="mt-0.5 text-zinc-700">{item.jd_requirement}</p>
                          </div>
                        )}
                        <div>
                          <p className="font-semibold text-zinc-500">怎么改</p>
                          <p className="mt-0.5 text-zinc-700">{item.suggestion}</p>
                        </div>
                        <div>
                          <p className="font-semibold text-zinc-500">为什么要改</p>
                          {item.evidence.length > 0 ? (
                            <ul className="mt-0.5 space-y-0.5 text-zinc-600">
                              {item.evidence.map((line, evidenceIndex) => (
                                <li key={evidenceIndex}>· {line}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="mt-0.5 text-zinc-600">
                              {reason || '简历档案标注此处存在描述缺口。'}
                            </p>
                          )}
                        </div>
                        <div>
                          <p className="font-semibold text-zinc-500">简历原文</p>
                          <p
                            className={`mt-0.5 ${
                              excerpt ? 'text-zinc-600' : 'text-zinc-400'
                            }`}
                          >
                            {excerpt || '未定位到原文，请按项目名核对。'}
                          </p>
                        </div>
                        {item.example && (
                          <div>
                            <p className="font-semibold text-zinc-500">参考写法</p>
                            <p className="mt-0.5 rounded-lg bg-white px-3 py-2 text-zinc-700 ring-1 ring-zinc-200">
                              {item.example}
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                )
              },
            )}
          </ul>
          {advice.suggestions.length > 3 && (
            <button
              type="button"
              className="mt-3 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-600 transition hover:border-zinc-300 hover:text-zinc-900"
              onClick={() => {
                setShowAll((current) => !current)
                setExpanded(-1)
              }}
            >
              {showAll ? '收起建议' : `查看全部 ${advice.suggestions.length} 条`}
            </button>
          )}
        </>
      )}
    </section>
  )
}
