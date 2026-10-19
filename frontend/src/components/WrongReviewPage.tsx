import {
  AlertTriangle,
  ArrowLeft,
  BookOpenCheck,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  GraduationCap,
  Layers,
  Loader2,
  Mic,
  RefreshCw,
  RotateCcw,
  Search,
  TrendingUp,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  generateQuizPaper,
  generateReviewPractice,
  getQuizDocumentProgress,
  getQuizProgress,
  getReviewDashboard,
  listReviewRecords,
  retryReviewSync,
  saveReviewErrorCause,
} from '../api'
import type {
  QuizPaper,
  QuizDocumentProgress,
  QuizProgress,
  ReviewDashboard,
  ReviewRecord,
  ReviewStatus,
  TopicProgress,
} from '../types'

interface WrongReviewPageProps {
  onPaperReady: (paper: QuizPaper) => void
  onBackToQuiz: () => void
  onOpenExpression: (
    topic: string,
    module?: string,
    sourceDocIds?: string[],
    sourceTag?: string,
    reviewId?: string,
  ) => void
}

type ReviewTab = 'today' | 'knowledge' | 'all'
type RecordStatusFilter = 'all' | ReviewStatus

const TYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

const STATUS_LABEL: Record<ReviewStatus, string> = {
  active: '待巩固',
  graduated: '已毕业',
  archived: '已归档',
}

const ERROR_CAUSES = ['概念不清', '概念混淆', '粗心', '超纲'] as const
const REVIEW_TABS: { value: ReviewTab; label: string; icon: typeof CalendarClock }[] = [
  { value: 'today', label: '今日复习', icon: CalendarClock },
  { value: 'knowledge', label: '知识地图', icon: Layers },
  { value: 'all', label: '全部错题', icon: BookOpenCheck },
]

const moduleLabel = (module: string) =>
  module === '综合测试能力' ? '传统软件测试' : module || '待分类'

function formatDate(timestamp: number): string {
  if (!timestamp) return '—'
  return new Date(timestamp * 1000).toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  })
}

function accuracyColor(accuracy: number): string {
  if (accuracy >= 0.8) return 'bg-emerald-600'
  if (accuracy >= 0.6) return 'bg-amber-500'
  return 'bg-red-500'
}
function topicPriorityScore(topic: TopicProgress): number {
  const lastAnswerCorrect = topic.streak === 0 ? 0 : 1
  return topic.accuracy * 0.65 + lastAnswerCorrect * 0.35
}

function priorityAdvice(topic: TopicProgress): string {
  const percent = Math.round(topic.accuracy * 100)
  const missingTypes = (['single', 'multiple', 'judge'] as const).filter(
    (type) => (topic.by_type?.[type]?.correct ?? 0) === 0,
  )
  const missingLabels = missingTypes.map((type) => TYPE_LABEL[type]).join('、')

  if (topic.total < 4) {
    return `当前只有 ${topic.total} 道样本，先补 ${4 - topic.total} 道后再判断；已通过题型要和新题型混合练。`
  }
  if (topic.accuracy < 0.6) {
    return `正确率仅 ${percent}%，先回读对应原资料和错题解析，再用单选题、判断题补基础，不要直接上难题。`
  }
  if (missingTypes.length > 0 && !topic.cross_type_verified) {
    return `已通过的题型还不够，建议下一套优先加入${missingLabels}，确认换题型后仍能讲清。`
  }
  if (topic.streak < 2) {
    return `当前正确率 ${percent}%，但最近连续答对不足 2 次；再做一组混合题确认不是短期记忆。`
  }
  if (topic.accuracy < 0.85) {
    return `正确率 ${percent}%，尚未达到 85%；优先用错题场景变形，隔天再复测一次。`
  }
  return `当前正确率 ${percent}%，保持 1 天 / 3 天 / 7 天 / 15 天间隔复习即可。`
}
export function WrongReviewPage({
  onPaperReady,
  onBackToQuiz,
  onOpenExpression,
}: WrongReviewPageProps) {
  const [tab, setTab] = useState<ReviewTab>('today')
  const [dashboard, setDashboard] = useState<ReviewDashboard | null>(null)
  const [records, setRecords] = useState<ReviewRecord[]>([])
  const [progress, setProgress] = useState<QuizProgress | null>(null)
  const [documentProgress, setDocumentProgress] = useState<QuizDocumentProgress | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [practiceBusy, setPracticeBusy] = useState('')
  const [savingCause, setSavingCause] = useState('')
  const [expandedTask, setExpandedTask] = useState('')
  const [statusFilter, setStatusFilter] = useState<RecordStatusFilter>('all')
  const [keyword, setKeyword] = useState('')
  const [selectedModule, setSelectedModule] = useState('')
  const [knowledgeView, setKnowledgeView] = useState<'module' | 'document'>('module')
  const [expandedDocument, setExpandedDocument] = useState('')
  const [documentPracticeBusy, setDocumentPracticeBusy] = useState('')
  const [syncBusy, setSyncBusy] = useState(false)
  const [syncMessage, setSyncMessage] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextDashboard, nextRecords, nextProgress, nextDocumentProgress] = await Promise.all([
        getReviewDashboard(),
        listReviewRecords(),
        getQuizProgress().catch(() => null),
        getQuizDocumentProgress().catch(() => null),
      ])
      setDashboard(nextDashboard)
      setRecords(nextRecords)
      setProgress(nextProgress)
      setDocumentProgress(nextDocumentProgress)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '复盘数据加载失败。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const handlePractice = async (topicId: string, includeToday = false) => {
    setPracticeBusy(topicId)
    setError('')
    try {
      onPaperReady(await generateReviewPractice(topicId, includeToday))
    } catch (practiceError) {
      setError(practiceError instanceof Error ? practiceError.message : '复盘练习组卷失败。')
    } finally {
      setPracticeBusy('')
    }
  }

  const handleDocumentPractice = async (docId: string) => {
    const doc = documentProgress?.documents.find((item) => item.doc_id === docId)
    if (!doc) return
    const keywords = doc.weak_topics
      .slice()
      .sort((left, right) => left.accuracy - right.accuracy || right.total - left.total)
      .slice(0, 5)
      .map((item) => item.topic)
    setDocumentPracticeBusy(docId)
    setError('')
    try {
      onPaperReady(
        await generateQuizPaper({
          keywords: keywords.length > 0 ? keywords : [doc.title],
          doc_ids: [docId],
          single: 5,
          multiple: 3,
          judge: 2,
          difficulty: 'mixed',
          focus_weak: true,
          mode: 'normal',
        }),
      )
    } catch (practiceError) {
      setError(
        practiceError instanceof Error
          ? practiceError.message
          : '文档定向组卷失败。',
      )
    } finally {
      setDocumentPracticeBusy('')
    }
  }

  const handleSaveCause = async (record: ReviewRecord, cause: string) => {
    setSavingCause(record.review_id)
    setError('')
    try {
      await saveReviewErrorCause(record.review_id, cause)
      setRecords((current) =>
        current.map((item) =>
          item.review_id === record.review_id
            ? {
                ...item,
                error_causes: [...(item.error_causes ?? []), cause].slice(-20),
              }
            : item,
        ),
      )
      setDashboard((current) =>
        current
          ? {
              ...current,
              tasks: current.tasks.map((task) => ({
                ...task,
                records: task.records.map((item) =>
                  item.review_id === record.review_id
                    ? {
                        ...item,
                        error_causes: [...(item.error_causes ?? []), cause].slice(-20),
                      }
                    : item,
                ),
              })),
            }
          : current,
      )
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '错因标记失败。')
    } finally {
      setSavingCause('')
    }
  }

  const recordDocIds = (record: ReviewRecord): string[] => {
    const lineage = record.source_lineage ?? {}
    return Array.from(
      new Set(
        [
          lineage.source_doc_id,
          ...(lineage.paper_doc_ids ?? []),
          ...(lineage.source_docs ?? []).map((doc) => doc.doc_id),
        ].filter((id): id is string => Boolean(id)),
      ),
    )
  }

  const handleRetrySync = async () => {
    setSyncBusy(true)
    setSyncMessage('')
    try {
      const result = await retryReviewSync()
      setSyncMessage(`已重试 ${result.retried} 项，剩余 ${result.pending_count} 项。`)
      await loadData()
    } catch (retryError) {
      setSyncMessage(retryError instanceof Error ? retryError.message : '重试失败。')
    } finally {
      setSyncBusy(false)
    }
  }

  const filteredRecords = useMemo(() => {
    const text = keyword.trim().toLowerCase()
    return records
      .filter((item) => {
        if (statusFilter !== 'all' && item.status !== statusFilter) return false
        if (!text) return true
        return [item.topic, item.stem, item.source_title, item.correct_answer_text]
          .filter((value): value is string => Boolean(value))
          .some((value) => value.toLowerCase().includes(text))
      })
      .sort(
        (left, right) =>
          (right.updated_at ?? 0) - (left.updated_at ?? 0) ||
          (right.created_at ?? 0) - (left.created_at ?? 0),
      )
  }, [keyword, records, statusFilter])

  const moduleStats = progress?.module_stats ?? []
  const activeModule = selectedModule || moduleStats[0]?.module || ''
  const moduleTopics = (progress?.topics ?? []).filter(
    (item) => moduleLabel(item.module || '') === activeModule,
  )
  const priorityTopics = activeModule
    ? moduleTopics
        .filter(
          (topic) =>
            topic.total < 4 ||
            topic.accuracy < 0.85 ||
            topic.streak < 2 ||
            !topic.cross_type_verified,
        )
        .slice()
        .sort(
          (left, right) =>
            topicPriorityScore(left) - topicPriorityScore(right) ||
            (right.updated_at ?? 0) - (left.updated_at ?? 0) ||
            right.total - left.total,
        )
        .slice(0, 8)
    : []
  return (
    <div className="space-y-5">
      <section className="tool-card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
              <BookOpenCheck className="size-5 text-emerald-600" aria-hidden="true" />
              错题复盘
            </h2>
            <p className="mt-1 text-sm text-zinc-500">
              普通刷题答对不移出复盘，复盘练习通过才进入下一段间隔。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {dashboard && (
              <span className="rounded-lg bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700">
                今日待复盘 {dashboard.due_count} · 待巩固 {dashboard.active_count} ·
                已毕业 {dashboard.graduated_count}
              </span>
            )}
            {dashboard && (dashboard.today_new_count ?? 0) > 0 && (
              <span className="rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800">
                今日新错 {dashboard.today_new_count}
              </span>
            )}
            {dashboard && (
              <span className="rounded-lg bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                连续完成 {dashboard.current_streak ?? 0} 天
              </span>
            )}
            {(dashboard?.sync_pending_count ?? 0) > 0 && (
              <button
                type="button"
                className="secondary-btn px-3 py-2 text-xs"
                onClick={() => void handleRetrySync()}
                disabled={syncBusy}
              >
                {syncBusy ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <RefreshCw className="size-3.5" aria-hidden="true" />
                )}
                重试同步（{dashboard?.sync_pending_count}）
              </button>
            )}
            <button type="button" className="secondary-btn px-3 py-2 text-xs" onClick={onBackToQuiz}>
              <ArrowLeft className="size-3.5" aria-hidden="true" />
              回到在线刷题
            </button>
          </div>
        </div>

        {syncMessage && (
          <p className="mt-3 text-xs leading-5 text-zinc-600">{syncMessage}</p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {REVIEW_TABS.map((item) => {
            const Icon = item.icon
            const switchTab = () => {
              setTab(item.value)
            }
            return (
              <button
                key={item.value}
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm transition ${
                tab === item.value
                    ? 'border-emerald-500 bg-emerald-600 text-white'
                    : 'border-zinc-300 bg-white text-zinc-600 hover:border-emerald-300'
                }`}
                onClick={switchTab}
                aria-pressed={tab === item.value}
              >
                <Icon className="size-4" aria-hidden="true" />
                {item.label}
              </button>
            )
          })}
        </div>

      </section>

      {loading && (
        <section className="tool-card flex items-center gap-3 text-sm text-zinc-500">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          正在加载复盘数据
        </section>
      )}

      {!loading && error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      {!loading && tab === 'today' && (
        <section className="space-y-4">
          {dashboard && (dashboard.today_tasks?.length ?? 0) > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-zinc-900">今日新错动态</p>
                <span className="text-xs text-zinc-500">
                  今天新答错的知识点，可先即时巩固，明天再进入间隔复习
                </span>
              </div>
              {dashboard.today_tasks!.map((task) => {
                const expansionKey = `today:${task.topic_id}`
                const open = expandedTask === expansionKey
                return (
                  <section key={expansionKey} className="tool-card border-amber-200 bg-amber-50/40">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-zinc-900">{task.topic}</p>
                        <p className="mt-1 text-xs text-zinc-500">
                          今日新错 {task.due_count} 道 · 明日进入 1 天 / 3 天 / 7 天 / 15 天间隔
                        </p>
                        {task.source_docs && task.source_docs.length > 0 && (
                          <p className="mt-1 truncate text-xs text-zinc-500">
                            来源：《{task.source_docs[0]}》
                            {task.source_docs.length > 1 ? ` 等 ${task.source_docs.length} 份` : ''}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className="primary-btn px-3 py-2 text-xs"
                          onClick={() => void handlePractice(task.topic_id, true)}
                          disabled={practiceBusy === task.topic_id}
                        >
                          {practiceBusy === task.topic_id ? (
                            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                          ) : (
                            <RotateCcw className="size-3.5" aria-hidden="true" />
                          )}
                          马上练 5 题
                        </button>
                        <button
                          type="button"
                          className="secondary-btn px-3 py-2 text-xs"
                          onClick={() => setExpandedTask(open ? '' : expansionKey)}
                          aria-expanded={open}
                        >
                          {open ? (
                            <ChevronDown className="size-3.5" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="size-3.5" aria-hidden="true" />
                          )}
                          展开
                        </button>
                      </div>
                    </div>
                    {open && (
                      <ul className="mt-4 space-y-3">
                        {task.records.map((record) => (
                          <li key={record.review_id} className="rounded-lg border border-amber-200 bg-white p-3.5">
                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span className="rounded-md bg-white px-2 py-0.5 font-medium text-zinc-600 ring-1 ring-zinc-200">
                                {TYPE_LABEL[record.type] ?? record.type}
                              </span>
                              <span className="text-zinc-500">错 {record.wrong_count} 次</span>
                            </div>
                            <p className="mt-2 text-sm leading-6 text-zinc-800">{record.stem}</p>
                            <p className="mt-2 text-sm leading-6 text-emerald-800">
                              正确答案：{record.correct_answer_text}
                            </p>
                            {record.explanation && (
                              <p className="mt-1.5 text-sm leading-6 text-zinc-600">{record.explanation}</p>
                            )}
                            <div className="mt-3 flex justify-end">
                              <button
                                type="button"
                                className="inline-flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700"
                                onClick={() =>
                                  onOpenExpression(
                                    record.topic,
                                    undefined,
                                    recordDocIds(record),
                                    'wrong_review',
                                    record.review_id,
                                  )
                                }
                              >
                                <Mic className="size-3" aria-hidden="true" />
                                去说一说
                              </button>
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                )
              })}
            </div>
          )}
          {dashboard && dashboard.tasks.length === 0 && (dashboard.today_tasks?.length ?? 0) === 0 && (
            <section className="tool-card text-sm text-zinc-500">
              今天没有到期错题，也没有新的错题动态。继续保持每天练习，复盘会按 1 天 → 3 天 → 7 天 → 15 天自动排期。
            </section>
          )}
          {dashboard && dashboard.tasks.length > 0 && (
            <p className="text-sm font-semibold text-zinc-900">到期复习</p>
          )}
          {dashboard?.tasks.map((task) => {
            const open = expandedTask === task.topic_id
            return (
              <section key={task.topic_id} className="tool-card">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-zinc-900">{task.topic}</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      到期 {task.due_count} 道 · 1 天 / 3 天 / 7 天 / 15 天间隔
                    </p>
                    {task.source_docs && task.source_docs.length > 0 && (
                      <p className="mt-1 truncate text-xs text-zinc-500">
                        来源：《{task.source_docs[0]}》
                        {task.source_docs.length > 1 ? ` 等 ${task.source_docs.length} 份` : ''}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="primary-btn px-3 py-2 text-xs"
                      onClick={() => void handlePractice(task.topic_id)}
                      disabled={practiceBusy === task.topic_id}
                    >
                      {practiceBusy === task.topic_id ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <RotateCcw className="size-3.5" aria-hidden="true" />
                      )}
                      再练 5 题
                    </button>
                    <button
                      type="button"
                      className="secondary-btn px-3 py-2 text-xs"
                      onClick={() => setExpandedTask(open ? '' : task.topic_id)}
                      aria-expanded={open}
                    >
                      {open ? (
                        <ChevronDown className="size-3.5" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="size-3.5" aria-hidden="true" />
                      )}
                      展开
                    </button>
                  </div>
                </div>

                {open && (
                  <ul className="mt-4 space-y-3">
                    {task.records.map((record) => (
                      <li key={record.review_id} className="rounded-lg border border-zinc-200 bg-zinc-50 p-3.5">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="rounded-md bg-white px-2 py-0.5 font-medium text-zinc-600 ring-1 ring-zinc-200">
                            {TYPE_LABEL[record.type] ?? record.type}
                          </span>
                          <span className="text-zinc-500">错 {record.wrong_count} 次</span>
                          <span className="text-zinc-500">下次 {formatDate(record.next_review_at)}</span>
                        </div>
                        <p className="mt-2 text-sm leading-6 text-zinc-800">{record.stem}</p>
                        <p className="mt-2 text-sm leading-6 text-emerald-800">
                          正确答案：{record.correct_answer_text}
                        </p>
                        {record.explanation && (
                          <p className="mt-1.5 text-sm leading-6 text-zinc-600">{record.explanation}</p>
                        )}
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-zinc-700">标记错因</span>
                          {ERROR_CAUSES.map((cause) => (
                            <button
                              key={cause}
                              type="button"
                              className={`rounded-lg border px-2.5 py-1.5 text-xs transition ${
                                record.error_causes?.includes(cause)
                                  ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                                  : 'border-zinc-300 bg-white text-zinc-600 hover:border-emerald-300'
                              }`}
                              onClick={() => void handleSaveCause(record, cause)}
                              disabled={savingCause === record.review_id}
                            >
                              {record.error_causes?.includes(cause) && (
                                <Check className="mr-1 inline size-3" aria-hidden="true" />
                              )}
                              {cause}
                            </button>
                          ))}
                          <button
                            type="button"
                            className="ml-auto inline-flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700"
                            onClick={() =>
                              onOpenExpression(
                                record.topic,
                                undefined,
                                recordDocIds(record),
                                'wrong_review',
                                record.review_id,
                              )
                            }
                          >
                            <Mic className="size-3" aria-hidden="true" />
                            去说一说
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })}
        </section>
      )}

      {!loading && tab === 'knowledge' && (
        <section className="tool-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-semibold text-zinc-900">知识地图</p>
            <div
              className="flex rounded-lg border border-zinc-300 bg-white p-0.5"
              role="group"
              aria-label="知识地图视图"
            >
              <button
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  knowledgeView === 'module'
                    ? 'bg-emerald-600 text-white'
                    : 'text-zinc-600 hover:bg-zinc-100'
                }`}
                onClick={() => setKnowledgeView('module')}
                aria-pressed={knowledgeView === 'module'}
              >
                <Layers className="size-3.5" aria-hidden="true" />
                板块热力
              </button>
              <button
                type="button"
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  knowledgeView === 'document'
                    ? 'bg-emerald-600 text-white'
                    : 'text-zinc-600 hover:bg-zinc-100'
                }`}
                onClick={() => setKnowledgeView('document')}
                aria-pressed={knowledgeView === 'document'}
              >
                <FileText className="size-3.5" aria-hidden="true" />
                文档热力
              </button>
            </div>
          </div>

          {knowledgeView === 'document' && (
            <div className="mt-4">
              <h3 className="text-sm font-semibold text-zinc-900">文档热力</h3>
              <p className="mt-1 text-xs text-zinc-500">
                按原始知识文档聚合答题与待巩固错题，看整篇资料的整体掌握情况；归因口径为精确来源 / 单文档试卷 / 标题匹配。
              </p>
              {documentProgress && documentProgress.unattributed_total > 0 && (
                <p className="mt-1 text-xs text-amber-700">
                  另有 {documentProgress.unattributed_total} 道历史题暂无法归因到具体文档，未计入下方统计。
                </p>
              )}
              {!documentProgress ? (
                <p className="mt-4 text-sm text-zinc-500">文档统计暂不可用。</p>
              ) : documentProgress.documents.length === 0 ? (
                <p className="mt-4 text-sm text-zinc-500">
                  {documentProgress.unattributed_total > 0
                    ? `已有答题记录但暂时无法归因到文档（${documentProgress.unattributed_total} 道），先按单篇文档出题后再看整体掌握度。`
                    : '还没有针对单篇文档的答题记录，去在线刷题按文档出题后会在这里显示整体掌握度。'}
                </p>
              ) : (
                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {documentProgress.documents.map((doc) => {
                    const open = expandedDocument === doc.doc_id
                    const attributionParts = [
                      doc.attribution.exact > 0 ? `精确 ${doc.attribution.exact}` : '',
                      doc.attribution.paper > 0 ? `试卷 ${doc.attribution.paper}` : '',
                      doc.attribution.title > 0 ? `标题 ${doc.attribution.title}` : '',
                    ].filter(Boolean)
                    return (
                      <div key={doc.doc_id} className="rounded-lg border border-zinc-200 bg-zinc-50 p-3.5">
                        <div className="flex items-start justify-between gap-2">
                          <span
                            className="truncate text-sm font-medium text-zinc-900"
                            title={doc.title}
                          >
                            {doc.title}
                          </span>
                          <span className="shrink-0 text-xs font-semibold text-zinc-700">
                            {Math.round(doc.accuracy * 100)}%
                          </span>
                        </div>
                        <div className="mt-2 h-2 overflow-hidden rounded-lg bg-zinc-200">
                          <div
                            className={`h-full rounded-lg ${accuracyColor(doc.accuracy)}`}
                            style={{ width: `${Math.max(4, doc.accuracy * 100)}%` }}
                          />
                        </div>
                        <p className="mt-2 text-xs text-zinc-500">
                          {doc.total} 道 · {doc.attempt_count} 份答卷 ·{' '}
                          {doc.active_mistake_count} 道待巩固
                        </p>
                        {attributionParts.length > 0 && (
                          <p className="mt-1 text-xs text-zinc-400">
                            {attributionParts.join(' · ')}
                          </p>
                        )}
                        {doc.weak_topics.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {doc.weak_topics.slice(0, 3).map((topic) => (
                              <span
                                key={topic.topic}
                                className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800"
                              >
                                {topic.topic} {Math.round(topic.accuracy * 100)}%
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="mt-2 text-xs text-emerald-700">
                            薄弱知识点低于阈值，保持间隔复习。
                          </p>
                        )}
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            className="secondary-btn px-2.5 py-1.5 text-xs"
                            onClick={() => setExpandedDocument(open ? '' : doc.doc_id)}
                            aria-expanded={open}
                          >
                            {open ? (
                              <ChevronDown className="size-3.5" aria-hidden="true" />
                            ) : (
                              <ChevronRight className="size-3.5" aria-hidden="true" />
                            )}
                            {open ? '收起知识点' : '展开知识点'}
                          </button>
                          <button
                            type="button"
                            className="primary-btn px-2.5 py-1.5 text-xs"
                            onClick={() => void handleDocumentPractice(doc.doc_id)}
                            disabled={documentPracticeBusy === doc.doc_id}
                          >
                            {documentPracticeBusy === doc.doc_id ? (
                              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                            ) : (
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                            )}
                            针对这篇文档再练
                          </button>
                        </div>
                        {open && (
                          <ul className="mt-3 space-y-2 border-t border-zinc-200 pt-3">
                            {doc.topics.map((topic) => (
                              <li
                                key={topic.topic}
                                className="flex items-center justify-between gap-2 text-xs"
                              >
                                <span className="truncate text-zinc-700">{topic.topic}</span>
                                <span className="shrink-0 text-zinc-500">
                                  {topic.total} 道 · {Math.round(topic.accuracy * 100)}%
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {documentProgress && documentProgress.no_data_docs.length > 0 && (
                <div className="mt-4">
                  <p className="text-sm font-semibold text-zinc-900">尚未针对该文档练习</p>
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {documentProgress.no_data_docs.map((doc) => (
                      <li
                        key={doc.doc_id}
                        className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-xs text-zinc-600"
                      >
                        {doc.title}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {knowledgeView === 'module' && (
            <div className="mt-4">
              <h3 className="text-sm font-semibold text-zinc-900">板块热力</h3>
              <p className="mt-1 text-xs text-zinc-500">
                红色低于 60%，黄色 60%–79%，绿色 80% 及以上。数据来自真实答题记录。
              </p>
              {moduleStats.length === 0 ? (
                <p className="mt-4 text-sm text-zinc-500">还没有可展示的板块数据。</p>
              ) : (
                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {moduleStats.map((module) => {
                    const active = activeModule === module.module
                    return (
                      <button
                        key={module.module}
                        type="button"
                        className={`rounded-lg border p-3.5 text-left transition ${
                          active
                            ? 'border-emerald-500 bg-emerald-50'
                            : 'border-zinc-200 bg-zinc-50 hover:border-emerald-200'
                        }`}
                        onClick={() => setSelectedModule(module.module)}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium text-zinc-900">{moduleLabel(module.module)}</span>
                          <span className="shrink-0 text-xs text-zinc-500">
                            {Math.round(module.accuracy * 100)}%
                          </span>
                        </div>
                        <div className="mt-2 h-2 overflow-hidden rounded-lg bg-zinc-200">
                          <div
                            className={`h-full rounded-lg ${accuracyColor(module.accuracy)}`}
                            style={{ width: `${Math.max(4, module.accuracy * 100)}%` }}
                          />
                        </div>
                        <p className="mt-2 text-xs text-zinc-500">
                          {module.topic_count} 个知识点 · {module.active_mistake_count} 道待巩固
                        </p>
                      </button>
                    )
                  })}
                </div>
              )}
              {activeModule && (
                <div className="mt-5">
                  <p className="text-sm font-semibold text-zinc-900">{moduleLabel(activeModule)}</p>
                  {moduleTopics.length === 0 ? (
                    <p className="mt-2 text-sm text-zinc-500">这个板块还没有知识点样本。</p>
                  ) : (
                    <ul className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                      {moduleTopics.map((topic) => (
                        <li key={topic.topic} className="rounded-lg bg-zinc-50 p-3">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs font-medium text-zinc-800">{topic.topic}</span>
                            <span className="shrink-0 text-xs text-zinc-500">
                              {Math.round(topic.accuracy * 100)}%
                            </span>
                          </div>
                          <div className="mt-1.5 h-1.5 overflow-hidden rounded-lg bg-zinc-200">
                            <div
                              className={`h-full rounded-lg ${accuracyColor(topic.accuracy)}`}
                              style={{ width: `${Math.max(4, topic.accuracy * 100)}%` }}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {activeModule && (
                <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3.5">
                  <p className="flex items-center gap-1.5 text-sm font-semibold text-amber-900">
                    <TrendingUp className="size-4" aria-hidden="true" />
                    优先巩固 · {moduleLabel(activeModule)}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-amber-800/80">
                    根据当前板块的样本量、正确率、题型覆盖和最近答题状态实时排序。
                  </p>
                  {priorityTopics.length === 0 ? (
                    <p className="mt-2 text-sm leading-6 text-amber-900">
                      当前板块暂无明显薄弱点，保持间隔复习即可。
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2">
                      {priorityTopics.map((topic) => (
                        <li key={topic.topic} className="text-sm leading-6 text-amber-900">
                          <span className="font-medium">{topic.topic}</span>：{priorityAdvice(topic)}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {!loading && tab === 'all' && (
        <section className="tool-card">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-52 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-400" aria-hidden="true" />
              <input
                className="text-input pl-9"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索知识点、题干或来源"
              />
            </div>
            <select
              aria-label="按复盘状态筛选"
              className="h-11 rounded-lg border border-zinc-300 bg-white px-3 text-sm text-zinc-700"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as RecordStatusFilter)}
            >
              <option value="all">全部状态</option>
              <option value="active">待巩固</option>
              <option value="graduated">已毕业</option>
              <option value="archived">已归档</option>
            </select>
          </div>

          {filteredRecords.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">没有匹配的复盘记录。</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {filteredRecords.map((record) => (
                <li key={record.review_id} className="rounded-lg border border-zinc-200 p-3.5">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-md bg-zinc-100 px-2 py-0.5 font-medium text-zinc-700">
                      {TYPE_LABEL[record.type] ?? record.type}
                    </span>
                    <span className="rounded-md bg-zinc-100 px-2 py-0.5 text-zinc-600">
                      {record.topic}
                    </span>
                    <span
                      className={`rounded-md px-2 py-0.5 font-medium ${
                        record.status === 'graduated'
                          ? 'bg-emerald-100 text-emerald-700'
                          : record.status === 'archived'
                            ? 'bg-zinc-100 text-zinc-500'
                            : 'bg-amber-100 text-amber-800'
                      }`}
                    >
                      {STATUS_LABEL[record.status] ?? record.status}
                    </span>
                    <span className="text-zinc-500">
                      错 {record.wrong_count} 次 · 下次 {formatDate(record.next_review_at)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-zinc-800">{record.stem}</p>
                  <p className="mt-1.5 text-xs leading-5 text-emerald-800">
                    正确答案：{record.correct_answer_text}
                  </p>
                  {record.error_causes && record.error_causes.length > 0 && (
                    <p className="mt-1.5 text-xs text-zinc-500">
                      错因：{record.error_causes.join('、')}
                    </p>
                  )}
                  {record.source_title && (
                    <p className="mt-1.5 truncate text-xs text-zinc-500">来源：{record.source_title}</p>
                  )}
                  <button
                    type="button"
                    className="mt-2 inline-flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1 text-xs font-semibold text-white transition hover:bg-sky-700"
                    onClick={() =>
                      onOpenExpression(
                        record.topic,
                        undefined,
                        recordDocIds(record),
                        'wrong_review',
                        record.review_id,
                      )
                    }
                  >
                    <Mic className="size-3" aria-hidden="true" />
                    去说一说
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {!loading && tab === 'knowledge' && progress && (
        <section className="tool-card flex items-start gap-2 text-sm text-zinc-500">
          <GraduationCap className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden="true" />
          <span>
            多数板块达到 85% 且薄弱知识点清零后，更适合进入模拟面试训练；当前系统建议：
            {progress.interview_ready ? '可以先做模拟面试。' : '继续刷题巩固。'}
          </span>
        </section>
      )}
    </div>
  )
}
