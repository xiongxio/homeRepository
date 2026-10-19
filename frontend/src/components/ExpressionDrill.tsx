import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  EyeOff,
  Flame,
  ListChecks,
  Loader2,
  Mic,
  Redo2,
  RefreshCw,
  Search,
  Square,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  analyzeExpression,
  getExpressionProgress,
  getExpressionQuestion,
  listInterviewRoles,
  listReviewRecords,
  transcribeExpressionClip,
} from '../api'
import type {
  ExpressionQuestionType,
  ExpressionProgress,
  ExpressionQuestion,
  ExpressionPracticeMode,
  ExpressionSourceType,
  ExpressionSession,
  InterviewRoleOption,
  KnowledgeDocument,
  ResumeRecord,
  ReviewRecord,
} from '../types'

const SCORE_LABEL: Record<string, string> = {
  fluency: '流畅度',
  structure: '结构化',
  confidence: '笃定感',
}

const HINT_MODES: Array<{
  key: ExpressionPracticeMode
  label: string
  description: string
  icon: typeof BookOpen
}> = [
  { key: 'read', label: '照读答案', description: '完整展示题库参考答案，先照着读，找停顿、节奏和表达感觉。', icon: BookOpen },
  { key: 'keywords', label: '关键词', description: '只保留答案线索，自己把关键词串成一段完整回答。', icon: ListChecks },
  { key: 'blind', label: '无提示', description: '不展示答案，按真实面试状态直接表达，用于判断是否真正掌握。', icon: EyeOff },
]

const MODE_LABEL: Record<ExpressionPracticeMode, string> = {
  read: '照读答案',
  keywords: '关键词串联',
  blind: '无提示实战',
}

const EXPRESSION_SOURCES: Array<{
  key: ExpressionSourceType
  label: string
  hint: string
}> = [
  { key: 'bank', label: '真题题库', hint: '从岗位真题库抽题' },
  { key: 'knowledge', label: '知识库文档', hint: '从指定文档生成表达题' },
  { key: 'mistake', label: '错题复盘', hint: '围绕同一道错题展开' },
  { key: 'resume', label: '简历', hint: '从项目、技能和亮点出题' },
]

const QUESTION_TYPE_OPTIONS: Array<{
  key: ExpressionQuestionType | ''
  label: string
  hint: string
}> = [
  { key: '', label: '不限', hint: '按原文最合适的角度出题' },
  { key: 'concept', label: '概念解释', hint: '讲清定义、原理和边界' },
  { key: 'compare', label: '对比辨析', hint: '讲清差异和选择依据' },
  { key: 'project', label: '项目深挖', hint: '追问职责、取舍和难点' },
  { key: 'scenario', label: '场景推演', hint: '结合事实判断如何处理' },
  { key: 'contribution', label: '结果贡献', hint: '聚焦成果、影响和验证' },
]

const REVIEW_RANGES: Array<{
  key: 'all' | '1d' | '3d' | '7d' | '14d' | '30d'
  label: string
  days: number
}> = [
  { key: 'all', label: '全部', days: 0 },
  { key: '1d', label: '近1天', days: 1 },
  { key: '3d', label: '近3天', days: 3 },
  { key: '7d', label: '近1周', days: 7 },
  { key: '14d', label: '近2周', days: 14 },
  { key: '30d', label: '近1月', days: 30 },
]

const KNOWLEDGE_POOL_LIMIT = 5

type ReviewRangeKey = (typeof REVIEW_RANGES)[number]['key']

// 复盘记录的时间戳由后端以秒返回，统一转成毫秒后再比较和格式化。
function toMilliseconds(value: number): number {
  if (!value) return 0
  return value < 1_000_000_000_000 ? value * 1000 : value
}

function reviewActivityAt(record: ReviewRecord): number {
  return Math.max(
    toMilliseconds(record.updated_at || 0),
    toMilliseconds(record.last_review_at || 0),
    toMilliseconds(record.created_at || 0),
  )
}

function formatReviewAge(timestamp: number): string {
  if (!timestamp) return ''
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const dayDiff = Math.floor((startOfToday - timestamp) / 86_400_000)
  if (dayDiff <= 0) return '今天'
  if (dayDiff === 1) return '昨天'
  if (dayDiff < 31) return `${dayDiff} 天前`
  const date = new Date(timestamp)
  if (date.getFullYear() !== now.getFullYear()) {
    return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
  }
  return `${date.getMonth() + 1}/${date.getDate()}`
}

interface ReviewGroup {
  key: string
  topic: string
  records: ReviewRecord[]
  representative: ReviewRecord
  latestAt: number
}

function reviewGroupKey(record: ReviewRecord): string {
  return (record.topic_id || record.topic || record.review_id).trim()
}

function buildReviewGroups(records: ReviewRecord[]): ReviewGroup[] {
  const buckets = new Map<string, ReviewRecord[]>()
  for (const record of records) {
    const key = reviewGroupKey(record)
    const bucket = buckets.get(key)
    if (bucket) {
      bucket.push(record)
    } else {
      buckets.set(key, [record])
    }
  }
  return Array.from(buckets.entries())
    .map(([key, groupRecords]) => {
      const sorted = [...groupRecords].sort(
        (left, right) => reviewActivityAt(right) - reviewActivityAt(left),
      )
      return {
        key,
        topic: sorted[0]?.topic || '未分类',
        records: sorted,
        representative: sorted[0],
        latestAt: reviewActivityAt(sorted[0]),
      }
    })
    .sort((left, right) => right.latestAt - left.latestAt)
}

function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

function ExpressionPointReviewCard({
  pointReview,
  onRetry,
}: {
  pointReview?: ExpressionSession['point_review']
  onRetry: () => void
}) {
  if (!pointReview || pointReview.total_points === 0) return null

  return (
    <div className="mt-4 rounded-lg border border-zinc-200 bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-zinc-900">要点命中</h3>
          <p className="mt-1 text-xs leading-5 text-zinc-500">
            命中 {pointReview.hit_count}/{pointReview.total_points} · 覆盖率 {pointReview.coverage_rate}%
          </p>
        </div>
        {pointReview.missed_points.length > 0 && (
          <button
            type="button"
            className="secondary-btn px-3 py-1.5 text-xs"
            onClick={onRetry}
          >
            <Redo2 className="size-3.5" aria-hidden="true" />
            针对遗漏再说一遍
          </button>
        )}
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-lg bg-zinc-100">
        <div
          className={`h-full rounded-lg ${
            pointReview.coverage_rate >= 80
              ? 'bg-emerald-600'
              : pointReview.coverage_rate >= 50
                ? 'bg-amber-500'
                : 'bg-red-500'
          }`}
          style={{ width: `${pointReview.coverage_rate}%` }}
        />
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="space-y-2">
          {pointReview.hit_points.map((item) => (
            <div key={item.point_id} className="rounded-lg border border-emerald-200 bg-emerald-50 p-2.5">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden="true" />
                <div>
                  <p className="text-sm leading-6 text-emerald-950">{item.text}</p>
                  <p className="mt-0.5 text-xs leading-5 text-emerald-700">{item.evidence}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
        <div className="space-y-2">
          {pointReview.missed_points.map((item) => (
            <div key={item.point_id} className="rounded-lg border border-red-200 bg-red-50 p-2.5">
              <div className="flex items-start gap-2">
                <XCircle className="mt-0.5 size-4 shrink-0 text-red-500" aria-hidden="true" />
                <div>
                  <p className="text-sm leading-6 text-red-950">{item.text}</p>
                  <p className="mt-0.5 text-xs leading-5 text-red-700">{item.evidence}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {pointReview.error && (
        <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-amber-700">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {pointReview.error}
        </p>
      )}
    </div>
  )
}

function ExpressionComparisonCard({ comparison }: { comparison?: ExpressionSession['comparison'] }) {
  if (!comparison) return null

  return (
    <div className="mt-3 rounded-lg border border-sky-200 bg-sky-50 p-3">
      <p className="text-sm font-medium text-sky-950">第 {comparison.previous_attempt_no} 次 vs 第 {comparison.current_attempt_no} 次</p>
      <div className="mt-2 flex items-center gap-2 text-sm text-sky-900">
        <span className="rounded bg-white px-2 py-1">
          上一次 {comparison.previous.coverage_rate}%
        </span>
        <span aria-hidden="true">→</span>
        <span className="rounded bg-sky-100 px-2 py-1 font-medium">
          本次 {comparison.current.coverage_rate}%
        </span>
      </div>
      {comparison.still_missed.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-sky-800">
          仍需补讲：{comparison.still_missed.join('；')}
        </p>
      )}
    </div>
  )
}

interface ExpressionDrillProps {
  seedTopic?: string
  seedRoleKey?: string
  seedToken?: number
  seedSourceDocIds?: string[]
  seedSourceTag?: string
  seedSourceType?: string
  seedSourceRef?: string
  documents?: KnowledgeDocument[]
  activeResume?: ResumeRecord | null
}

interface ExpressionSourceContext {
  docIds: string[]
  sourceTag: string
  sourceType: string
  sourceRef: string
  questionType: string
}

interface KnowledgePoolState {
  questions: ExpressionQuestion[]
  index: number
  exhausted: boolean
  message: string
}

const EMPTY_KNOWLEDGE_POOL: KnowledgePoolState = {
  questions: [],
  index: 0,
  exhausted: false,
  message: '',
}

function emptySourceContext(): ExpressionSourceContext {
  return {
    docIds: [],
    sourceTag: '',
    sourceType: 'bank',
    sourceRef: '',
    questionType: '',
  }
}

function normalizeSourceType(value: string): ExpressionSourceType {
  return value === 'knowledge' || value === 'mistake' || value === 'resume'
    ? value
    : 'bank'
}

function knowledgeSignature(docIds: string[], questionType: string): string {
  return `${[...docIds].sort().join('|')}::${questionType || 'any'}`
}

export function ExpressionDrill({
  seedTopic = '',
  seedRoleKey,
  seedToken = 0,
  seedSourceDocIds = [],
  seedSourceTag = '',
  seedSourceType = '',
  seedSourceRef = '',
  documents = [],
  activeResume = null,
}: ExpressionDrillProps) {
  const [roles, setRoles] = useState<InterviewRoleOption[]>([])
  const [roleKey, setRoleKey] = useState(
    () => seedRoleKey ?? window.localStorage.getItem('opc-expression-role') ?? 'ai_qa',
  )
  const [hintMode, setHintMode] = useState<ExpressionPracticeMode>(() => {
    const saved = window.localStorage.getItem('opc-expression-hint-mode')
    return saved === 'read' || saved === 'keywords' || saved === 'blind' ? saved : 'read'
  })
  const [question, setQuestion] = useState<ExpressionQuestion | null>(null)
  const [topicHint, setTopicHint] = useState(seedTopic)
  const [transcript, setTranscript] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [session, setSession] = useState<ExpressionSession | null>(null)
  const [previousSessionId, setPreviousSessionId] = useState('')
  const [retryMissedPoints, setRetryMissedPoints] = useState<string[]>([])
  const [progress, setProgress] = useState<ExpressionProgress | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [sourceType, setSourceType] = useState<ExpressionSourceType>(() =>
    normalizeSourceType(seedSourceType),
  )
  const [questionType, setQuestionType] = useState<ExpressionQuestionType | ''>('')
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>(seedSourceDocIds)
  const [selectedReviewId, setSelectedReviewId] = useState(seedSourceRef)
  const [reviewRecords, setReviewRecords] = useState<ReviewRecord[]>([])
  const [reviewSearch, setReviewSearch] = useState('')
  const [reviewRange, setReviewRange] = useState<ReviewRangeKey>('all')
  const [reviewPickerOpen, setReviewPickerOpen] = useState(!seedSourceRef)
  const [generatedKnowledgeSignature, setGeneratedKnowledgeSignature] = useState('')
  const [knowledgePool, setKnowledgePool] =
    useState<KnowledgePoolState>(EMPTY_KNOWLEDGE_POOL)
  const [knowledgeGenerating, setKnowledgeGenerating] = useState(false)
  const [resumeGenerating, setResumeGenerating] = useState(false)
  const [resumeRecord, setResumeRecord] = useState<ResumeRecord | null>(activeResume)
  const timerRef = useRef<number | null>(null)
  const seededTokenRef = useRef(seedToken)
  const activeSourceRef = useRef<ExpressionSourceContext>({
    docIds: seedSourceDocIds,
    sourceTag: seedSourceTag,
    sourceType: normalizeSourceType(seedSourceType),
    sourceRef: seedSourceRef,
    questionType: '',
  })
  const startedAtRef = useRef(0)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const shouldTranscribeRef = useRef(false)
  const answerRoundRef = useRef(0)
  const mistakeRefreshTimeoutRef = useRef<number | null>(null)
  const knowledgeRefreshTimeoutRef = useRef<number | null>(null)
  const resumeRefreshTimeoutRef = useRef<number | null>(null)
  const isRetrying = previousSessionId !== ''
  const reviewGroups = useMemo(() => buildReviewGroups(reviewRecords), [reviewRecords])
  const filteredReviewGroups = useMemo(() => {
    const tokens = reviewSearch.trim().toLowerCase().split(/\s+/).filter(Boolean)
    const range = REVIEW_RANGES.find((item) => item.key === reviewRange)
    const cutoff =
      range && range.days > 0 ? Date.now() - range.days * 86_400_000 : 0
    return reviewGroups
      .filter((group) => {
        if (cutoff && group.latestAt < cutoff) return false
        if (tokens.length === 0) return true
        const haystack = group.records
          .flatMap((record) => [
            record.topic,
            record.stem,
            record.source_title ?? '',
            ...(record.error_causes ?? []),
            ...(record.options ?? []).map((option) => option.text),
          ])
          .join(' ')
          .toLowerCase()
        return tokens.every((token) => haystack.includes(token))
      })
      .sort((left, right) => right.latestAt - left.latestAt)
  }, [reviewGroups, reviewSearch, reviewRange])
  const selectedReviewGroup = useMemo(
    () =>
      reviewGroups.find((group) =>
        group.records.some((record) => record.review_id === selectedReviewId),
      ) ?? null,
    [reviewGroups, selectedReviewId],
  )
  const currentKnowledgeSignature = knowledgeSignature(selectedDocIds, questionType)
  const knowledgeSelectionDirty =
    sourceType === 'knowledge' &&
    Boolean(question) &&
    generatedKnowledgeSignature !== '' &&
    generatedKnowledgeSignature !== currentKnowledgeSignature
  const knowledgePoolAtEnd =
    sourceType === 'knowledge' &&
    knowledgePool.questions.length > 0 &&
    knowledgePool.index >= knowledgePool.questions.length - 1
  const knowledgeGroupFull =
    knowledgePool.exhausted &&
    knowledgePool.questions.length >= KNOWLEDGE_POOL_LIMIT &&
    generatedKnowledgeSignature === currentKnowledgeSignature
  const [mediaSupported] = useState(
    () =>
      typeof navigator !== 'undefined' &&
      Boolean(navigator.mediaDevices?.getUserMedia) &&
      typeof MediaRecorder !== 'undefined',
  )

  const clearTimer = () => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  const cancelActiveRecording = useCallback(() => {
    answerRoundRef.current += 1
    shouldTranscribeRef.current = false
    clearTimer()
    streamRef.current?.getTracks().forEach((track) => track.stop())
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop()
    }
    streamRef.current = null
    recorderRef.current = null
    startedAtRef.current = 0
    chunksRef.current = []
    setElapsed(0)
  }, [])

  const refreshProgress = useCallback(async () => {
    try {
      setProgress(await getExpressionProgress())
    } catch {
      // 首次使用时还没有数据，静默即可。
    }
  }, [])

  const scheduleMistakeRefresh = useCallback(
    (
      key: string,
      hint: string,
      source: ExpressionSourceContext,
      round: number,
      attempt = 0,
    ) => {
      if (source.sourceType !== 'mistake' || attempt > 4) return
      if (mistakeRefreshTimeoutRef.current) {
        window.clearTimeout(mistakeRefreshTimeoutRef.current)
      }
      mistakeRefreshTimeoutRef.current = window.setTimeout(async () => {
        mistakeRefreshTimeoutRef.current = null
        if (answerRoundRef.current !== round) return
        try {
          const refreshed = await getExpressionQuestion(
            key,
            hint,
            source.docIds,
            source.sourceTag,
            source.sourceType,
            source.sourceRef,
            source.questionType,
          )
          if (answerRoundRef.current !== round) return
          if (refreshed.generation_pending) {
            scheduleMistakeRefresh(key, hint, source, round, attempt + 1)
            return
          }
          setQuestion(refreshed)
        } catch {
          // 后台生成失败时保留当前兜底题，不打断用户。
        }
      }, 15000)
    },
    [],
  )

  const scheduleKnowledgeRefresh = useCallback(
    (
      key: string,
      hint: string,
      source: ExpressionSourceContext,
      round: number,
      nonce: string,
      deadline: number,
    ) => {
      if (source.sourceType !== 'knowledge') return
      if (Date.now() > deadline) {
        setKnowledgeGenerating(false)
        setBusy('')
        setError('生成表达题超时，请稍后重试或更换文档。')
        return
      }
      if (knowledgeRefreshTimeoutRef.current) {
        window.clearTimeout(knowledgeRefreshTimeoutRef.current)
      }
      knowledgeRefreshTimeoutRef.current = window.setTimeout(async () => {
        knowledgeRefreshTimeoutRef.current = null
        if (answerRoundRef.current !== round) return
        try {
          const refreshed = await getExpressionQuestion(
            key,
            hint,
            source.docIds,
            source.sourceTag,
            source.sourceType,
            source.sourceRef,
            source.questionType,
            nonce,
          )
          if (answerRoundRef.current !== round) return
          if (refreshed.generation_pending) {
            scheduleKnowledgeRefresh(
              key,
              hint,
              source,
              round,
              nonce,
              deadline,
            )
            return
          }
          setQuestion(refreshed)
          setKnowledgePool({
            questions: refreshed.pool_questions ?? [],
            index: Math.max(0, (refreshed.pool_index ?? 1) - 1),
            exhausted: Boolean(refreshed.pool_exhausted),
            message: refreshed.pool_message ?? '',
          })
          setGeneratedKnowledgeSignature(
            knowledgeSignature(source.docIds, source.questionType),
          )
          setKnowledgeGenerating(false)
          setBusy('')
          setError('')
        } catch (refreshError) {
          if (answerRoundRef.current !== round) return
          setKnowledgeGenerating(false)
          setBusy('')
          setError(
            refreshError instanceof Error
              ? refreshError.message
              : '生成表达题失败，请稍后重试。',
          )
        }
      }, 1500)
    },
    [],
  )

  const scheduleResumeRefresh = useCallback(
    (
      key: string,
      hint: string,
      source: ExpressionSourceContext,
      round: number,
      deadline: number,
    ) => {
      if (source.sourceType !== 'resume') return
      if (Date.now() > deadline) {
        setResumeGenerating(false)
        setBusy('')
        setError('生成表达题超时，请稍后重试。')
        return
      }
      if (resumeRefreshTimeoutRef.current) {
        window.clearTimeout(resumeRefreshTimeoutRef.current)
      }
      resumeRefreshTimeoutRef.current = window.setTimeout(async () => {
        resumeRefreshTimeoutRef.current = null
        if (answerRoundRef.current !== round) return
        try {
          const refreshed = await getExpressionQuestion(
            key,
            hint,
            source.docIds,
            source.sourceTag,
            source.sourceType,
            source.sourceRef,
            source.questionType,
          )
          if (answerRoundRef.current !== round) return
          if (refreshed.generation_pending) {
            scheduleResumeRefresh(key, hint, source, round, deadline)
            return
          }
          setQuestion(refreshed)
          setResumeGenerating(false)
          setBusy('')
          setError('')
        } catch (refreshError) {
          if (answerRoundRef.current !== round) return
          setResumeGenerating(false)
          setBusy('')
          setError(
            refreshError instanceof Error
              ? refreshError.message
              : '生成表达题失败，请稍后重试。',
          )
        }
      }, 1500)
    },
    [],
  )

  const loadQuestion = useCallback(
    async (
      key: string,
      hint = '',
      source?: ExpressionSourceContext,
      nonce = '',
    ) => {
      const activeSource = source ?? activeSourceRef.current
      answerRoundRef.current += 1
      if (mistakeRefreshTimeoutRef.current) {
        window.clearTimeout(mistakeRefreshTimeoutRef.current)
        mistakeRefreshTimeoutRef.current = null
      }
      if (knowledgeRefreshTimeoutRef.current) {
        window.clearTimeout(knowledgeRefreshTimeoutRef.current)
        knowledgeRefreshTimeoutRef.current = null
      }
      if (resumeRefreshTimeoutRef.current) {
        window.clearTimeout(resumeRefreshTimeoutRef.current)
        resumeRefreshTimeoutRef.current = null
      }
      setKnowledgeGenerating(false)
      setResumeGenerating(false)
      cancelActiveRecording()
      const round = answerRoundRef.current
      setBusy('question')
      setError('')
      setQuestion(null)
      setSession(null)
      setTranscript('')
      setPreviousSessionId('')
      setRetryMissedPoints([])
      let pendingQuestion = false
      try {
        setTopicHint(hint)
        activeSourceRef.current = activeSource
        const nextQuestion = await getExpressionQuestion(
          key,
          hint,
          activeSource.docIds,
          activeSource.sourceTag,
          activeSource.sourceType,
          activeSource.sourceRef,
          activeSource.questionType,
          nonce,
        )
        if (
          activeSource.sourceType === 'knowledge' &&
          nextQuestion.generation_pending
        ) {
          pendingQuestion = true
          setQuestion(null)
          setKnowledgeGenerating(true)
          setError('')
          setBusy('question')
          scheduleKnowledgeRefresh(
            key,
            hint,
            activeSource,
            round,
            nonce || nextQuestion.pending_token || '',
            Date.now() + 180_000,
          )
          return
        }
        if (
          activeSource.sourceType === 'resume' &&
          nextQuestion.generation_pending
        ) {
          pendingQuestion = true
          setQuestion(null)
          setResumeGenerating(true)
          setError('')
          setBusy('question')
          scheduleResumeRefresh(
            key,
            hint,
            activeSource,
            round,
            Date.now() + 180_000,
          )
          return
        }
        setQuestion(nextQuestion)
        setKnowledgeGenerating(false)
        setResumeGenerating(false)
        if (activeSource.sourceType === 'knowledge') {
          setKnowledgePool({
            questions: nextQuestion.pool_questions ?? [],
            index: Math.max(0, (nextQuestion.pool_index ?? 1) - 1),
            exhausted: Boolean(nextQuestion.pool_exhausted),
            message: nextQuestion.pool_message ?? '',
          })
          setGeneratedKnowledgeSignature(
            knowledgeSignature(activeSource.docIds, activeSource.questionType),
          )
        } else {
          setKnowledgePool(EMPTY_KNOWLEDGE_POOL)
        }
        if (nextQuestion.generation_pending) {
          scheduleMistakeRefresh(key, hint, activeSource, round)
        }
      } catch (loadError) {
        setKnowledgeGenerating(false)
        setResumeGenerating(false)
        setError(loadError instanceof Error ? loadError.message : '取题失败。')
      } finally {
        if (!pendingQuestion) setBusy('')
      }
    },
    [
      cancelActiveRecording,
      scheduleKnowledgeRefresh,
      scheduleMistakeRefresh,
      scheduleResumeRefresh,
    ],
  )

  useEffect(
    () => () => {
      if (mistakeRefreshTimeoutRef.current) {
        window.clearTimeout(mistakeRefreshTimeoutRef.current)
      }
      if (knowledgeRefreshTimeoutRef.current) {
        window.clearTimeout(knowledgeRefreshTimeoutRef.current)
      }
      if (resumeRefreshTimeoutRef.current) {
        window.clearTimeout(resumeRefreshTimeoutRef.current)
      }
    },
    [],
  )

  useEffect(() => {
    listReviewRecords()
      .then(setReviewRecords)
      .catch(() => setReviewRecords([]))
  }, [])

  useEffect(() => {
    setResumeRecord(activeResume)
  }, [activeResume])

  useEffect(() => {
    listInterviewRoles().then((items) => {
      setRoles(items)
      const initial = items.some((item) => item.key === roleKey)
        ? roleKey
        : items[0]?.key ?? 'ai_qa'
      if (initial !== roleKey) {
        setRoleKey(initial)
      }
      // 首次取题必须使用挂载时的题源，不能读 ref：
      // 岗位接口返回前若用户切换了题源，ref 已变化，会带着空错题标识取题。
      void loadQuestion(initial, seedTopic, {
        docIds: seedSourceDocIds,
        sourceTag: seedSourceTag,
        sourceType: normalizeSourceType(seedSourceType),
        sourceRef: seedSourceRef,
        questionType: '',
      })
    })
    void refreshProgress()
    // 只在首次挂载时加载岗位和题目；切换岗位由 chooseRole 显式触发，避免重复取题。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const normalizedSeedSource = normalizeSourceType(seedSourceType)
    const hasSeedContext =
      seedToken > 0 &&
      seedToken !== seededTokenRef.current &&
      (Boolean(seedTopic) || normalizedSeedSource !== 'bank')
    if (!hasSeedContext) return

    seededTokenRef.current = seedToken
    const source = {
      docIds: seedSourceDocIds,
      sourceTag: normalizedSeedSource === 'bank' ? seedSourceTag : '',
      sourceType: normalizedSeedSource,
      sourceRef: seedSourceRef,
      questionType: '',
    }
    setSourceType(normalizedSeedSource)
    setQuestionType('')
    if (normalizedSeedSource === 'knowledge') setSelectedDocIds(seedSourceDocIds)
    if (normalizedSeedSource === 'mistake') {
      setSelectedReviewId(seedSourceRef)
      setReviewPickerOpen(false)
    }
    activeSourceRef.current = source

    if (normalizedSeedSource === 'knowledge' && seedSourceDocIds.length === 0) {
      setQuestion(null)
      setError('请先勾选至少一份知识库文档，再开始训练。')
      return
    }
    if (normalizedSeedSource === 'mistake' && !seedSourceRef) {
      setQuestion(null)
      setError('请选择一道错题。')
      return
    }
    void loadQuestion(roleKey, seedTopic, source)
    // seedTopic/roleKey 只是为本次新种子服务，避免旧话题在切换岗位后反复触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedToken])

  const buildSourceContext = (next?: Partial<ExpressionSourceContext>) => {
    const source: ExpressionSourceContext = {
      docIds: selectedDocIds,
      sourceTag: '',
      sourceType,
      sourceRef:
        sourceType === 'mistake'
          ? selectedReviewId
          : sourceType === 'resume'
            ? resumeRecord?.resume_id ?? ''
            : sourceType === 'knowledge'
              ? selectedDocIds.join('|')
              : '',
      questionType,
    }
    return { ...source, ...next }
  }

  const reloadWithSource = (source: ExpressionSourceContext, nonce = '') => {
    activeSourceRef.current = source
    void loadQuestion(roleKey, topicHint, source, nonce)
  }

  const changeSourceType = (next: ExpressionSourceType) => {
    if (
      busy === 'record' ||
      busy === 'transcribing' ||
      busy === 'analyze' ||
      busy === 'question'
    ) {
      return
    }
    setSourceType(next)
    const nextReviewId = selectedReviewId
    if (next === 'mistake') setReviewPickerOpen(true)
    const source: ExpressionSourceContext = {
      docIds: selectedDocIds,
      sourceTag: '',
      sourceType: next,
      sourceRef:
        next === 'mistake'
          ? nextReviewId
          : next === 'resume'
            ? resumeRecord?.resume_id ?? ''
            : next === 'knowledge'
              ? selectedDocIds.join('|')
              : '',
      questionType,
    }
    if (next === 'knowledge') {
      activeSourceRef.current = source
      setQuestion(null)
      setTopicHint('')
      setKnowledgePool(EMPTY_KNOWLEDGE_POOL)
      setGeneratedKnowledgeSignature('')
      setError(
        selectedDocIds.length === 0
          ? '请先勾选至少一份知识库文档，再点「生成 1 道表达题」。'
          : '',
      )
      return
    }
    if (next === 'mistake' && !nextReviewId) {
      activeSourceRef.current = source
      setQuestion(null)
      setError(
        reviewRecords.length > 0
          ? ''
          : '还没有可用的错题复盘记录，先在「在线刷题」完成答卷并同步错题。',
      )
      return
    }
    if (next === 'resume' && !resumeRecord) {
      activeSourceRef.current = source
      setQuestion(null)
      setError('暂无简历，请先在「简历管理」上传或粘贴简历后再使用该题源。')
      return
    }
    reloadWithSource(source)
  }

  const changeQuestionType = (next: ExpressionQuestionType | '') => {
    if (
      busy === 'record' ||
      busy === 'transcribing' ||
      busy === 'analyze' ||
      busy === 'question'
    ) {
      return
    }
    const source = buildSourceContext({ questionType: next })
    setQuestionType(next)
    if (source.sourceType === 'knowledge') {
      activeSourceRef.current = source
      setKnowledgePool(EMPTY_KNOWLEDGE_POOL)
      if (source.docIds.length === 0) {
        setQuestion(null)
        setError('请先勾选至少一份知识库文档，再选择题型。')
      } else {
        setError('')
      }
      return
    }
    if (source.sourceType === 'mistake' && !source.sourceRef) {
      activeSourceRef.current = source
      setQuestion(null)
      setError('请先选择一道错题，再选择题型。')
      return
    }
    if (source.sourceType === 'resume' && !resumeRecord) {
      activeSourceRef.current = source
      setQuestion(null)
      setError('暂无简历，请先在「简历管理」上传或粘贴简历后再选择题型。')
      return
    }
    reloadWithSource(source)
  }

  const toggleDocument = (docId: string) => {
    if (
      busy === 'record' ||
      busy === 'transcribing' ||
      busy === 'analyze' ||
      busy === 'question'
    ) {
      return
    }
    const next = selectedDocIds.includes(docId)
      ? selectedDocIds.filter((item) => item !== docId)
      : [...selectedDocIds, docId]
    setSelectedDocIds(next)
    if (sourceType !== 'knowledge') return
    activeSourceRef.current = buildSourceContext({
      docIds: next,
      sourceRef: next.join('|'),
    })
    setKnowledgePool(EMPTY_KNOWLEDGE_POOL)
    if (next.length === 0) {
      setQuestion(null)
      setError('请至少选择一份知识库文档。')
      return
    }
    setError('')
  }

  const applyKnowledgeQuestion = (index: number) => {
    const nextQuestion = knowledgePool.questions[index]
    if (!nextQuestion) return
    answerRoundRef.current += 1
    cancelActiveRecording()
    setSession(null)
    setTranscript('')
    setElapsed(0)
    setPreviousSessionId('')
    setRetryMissedPoints([])
    setError('')
    setQuestion(nextQuestion)
    setKnowledgePool((prev) => ({ ...prev, index }))
  }

  const generateKnowledgeQuestion = () => {
    if (
      busy === 'record' ||
      busy === 'transcribing' ||
      busy === 'analyze' ||
      busy === 'question'
    ) {
      return
    }
    if (selectedDocIds.length === 0) {
      setError('请先勾选至少一份知识库文档，再生成表达题。')
      return
    }
    const signature = knowledgeSignature(selectedDocIds, questionType)
    if (knowledgePool.exhausted && generatedKnowledgeSignature === signature) {
      setError(knowledgePool.message || '本组已练完，换文档或题型开始新一组。')
      return
    }
    const source = buildSourceContext()
    activeSourceRef.current = source
    reloadWithSource(
      source,
      `gen-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    )
  }

  const nextKnowledgeQuestion = () => {
    if (sourceType !== 'knowledge') return
    const nextIndex = knowledgePool.index + 1
    if (nextIndex < knowledgePool.questions.length) {
      applyKnowledgeQuestion(nextIndex)
      return
    }
    if (knowledgePool.exhausted) {
      setError(knowledgePool.message || '本组已练完，换文档或题型开始新一组。')
      return
    }
    generateKnowledgeQuestion()
  }

  const replayKnowledgePool = () => {
    if (knowledgePool.questions.length === 0) return
    applyKnowledgeQuestion(0)
  }

  const changeReviewRecord = (reviewId: string) => {
    setSelectedReviewId(reviewId)
    if (sourceType !== 'mistake') return
    const source = buildSourceContext({ sourceRef: reviewId })
    activeSourceRef.current = source
    if (!reviewId) {
      setQuestion(null)
      setError('请选择一道错题。')
      return
    }
    setReviewPickerOpen(false)
    reloadWithSource(source)
  }

  const chooseRole = (key: string) => {
    if (key === roleKey && (busy === 'record' || busy === 'transcribing')) return
    setRoleKey(key)
    window.localStorage.setItem('opc-expression-role', key)
    setSession(null)
    setSourceType('bank')
    setQuestionType('')
    setSelectedDocIds([])
    setSelectedReviewId('')
    const source = emptySourceContext()
    activeSourceRef.current = source
    void loadQuestion(key, '', source)
  }

  const chooseHintMode = (mode: ExpressionPracticeMode) => {
    if (mode === hintMode || busy === 'record' || busy === 'transcribing' || busy === 'analyze') return
    answerRoundRef.current += 1
    cancelActiveRecording()
    setHintMode(mode)
    window.localStorage.setItem('opc-expression-hint-mode', mode)
    setSession(null)
    setError('')
    setTranscript('')
    setElapsed(0)
    setPreviousSessionId('')
    setRetryMissedPoints([])
  }

  const resetAttempt = () => {
    answerRoundRef.current += 1
    cancelActiveRecording()
    setSession(null)
    setError('')
    setTranscript('')
    setElapsed(0)
  }

  const nextQuestionFromStart = () => {
    setHintMode('read')
    window.localStorage.setItem('opc-expression-hint-mode', 'read')
    if (sourceType === 'knowledge') {
      nextKnowledgeQuestion()
      return
    }
    void loadQuestion(roleKey, topicHint)
  }

  const followProgression = () => {
    if (!session?.progression) return
    const next = session.progression.next_mode
    if (next === 'next_question') {
      nextQuestionFromStart()
      return
    }
    chooseHintMode(next)
  }

  const retryMissedPointsAgain = () => {
    const missed = session?.point_review?.missed_points ?? []
    if (!session || missed.length === 0) return
    answerRoundRef.current += 1
    cancelActiveRecording()
    setHintMode('blind')
    window.localStorage.setItem('opc-expression-hint-mode', 'blind')
    setPreviousSessionId(session.session_id)
    setRetryMissedPoints(missed.map((item) => item.text))
    setSession(null)
    setError('')
    setTranscript('')
    setElapsed(0)
  }

  const handleProgressionClick = () => {
    if (!session?.progression) return
    if (session.progression.next_mode === hintMode) {
      resetAttempt()
      return
    }
    followProgression()
  }

  const startAnswer = async () => {
    if (!mediaSupported || busy === 'record' || busy === 'transcribing') return
    const round = answerRoundRef.current + 1
    answerRoundRef.current = round
    // 必须在 await 之前标记本轮有效，否则 getUserMedia 返回后会被误判为已取消。
    shouldTranscribeRef.current = true
    setError('')
    setSession(null)
    setTranscript('')
    setElapsed(0)
    setBusy('record')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (round !== answerRoundRef.current || !shouldTranscribeRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        if (round === answerRoundRef.current) setBusy('')
        return
      }
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
        (type) => MediaRecorder.isTypeSupported(type),
      )
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      streamRef.current = stream
      recorderRef.current = recorder
      shouldTranscribeRef.current = true

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        if (!shouldTranscribeRef.current || round !== answerRoundRef.current) return
        setBusy('transcribing')
        try {
          const blob = new Blob(chunksRef.current, {
            type: recorder.mimeType || mimeType || 'audio/webm',
          })
          const result = await transcribeExpressionClip(blob)
          if (round !== answerRoundRef.current) return
          setTranscript(result.text)
          setElapsed(Math.max(1, Math.round(result.duration)))
        } catch (transcribeError) {
          if (round !== answerRoundRef.current) return
          setError(
            transcribeError instanceof Error
              ? transcribeError.message
              : '本地语音识别失败，请重试或直接粘贴文字。',
          )
        } finally {
          chunksRef.current = []
          streamRef.current = null
          recorderRef.current = null
          startedAtRef.current = 0
          if (round === answerRoundRef.current) {
            shouldTranscribeRef.current = false
            setBusy('')
          }
        }
      }

      recorder.start(500)
      startedAtRef.current = Date.now()
      setElapsed(0)
      timerRef.current = window.setInterval(() => {
        if (!startedAtRef.current) return
        setElapsed(
          Math.max(0, Math.floor((Date.now() - startedAtRef.current) / 1000)),
        )
      }, 500)
    } catch (recordError) {
      if (round !== answerRoundRef.current) return
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      recorderRef.current = null
      setBusy('')
      if (recordError instanceof DOMException && recordError.name === 'NotAllowedError') {
        setError('麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotFoundError') {
        setError('没有检测到麦克风设备，可以直接在下方打字或粘贴转写。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotReadableError') {
        setError('麦克风暂时被其他应用占用，请关闭占用后重试。')
      } else {
        setError('无法启动麦克风录音，请检查设备权限，或直接打字作答。')
      }
    }
  }

  const stopAnswer = () => {
    clearTimer()
    const recorder = recorderRef.current
    const startedAt = startedAtRef.current
    if (startedAt > 0) {
      setElapsed(Math.max(1, Math.round((Date.now() - startedAt) / 1000)))
    }
    startedAtRef.current = 0
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
      return
    }
    // 录音器没启动成功时，结束按钮也必须能复位，不能把界面卡在录音态。
    shouldTranscribeRef.current = false
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    setBusy('')
  }

  useEffect(() => {
    return () => {
      shouldTranscribeRef.current = false
      clearTimer()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    }
  }, [])

  const submit = async () => {
    if (!question) return
    const content = transcript.trim()
    if (content.length < 10) {
      setError('回答太短了，先说够一句话再提交。')
      return
    }
    setBusy('analyze')
    setError('')
    try {
      const result = await analyzeExpression({
        role_key: roleKey,
        question: question.question,
        question_label: question.label,
        practice_mode: hintMode,
        standard_key_points: question.standard_key_points ?? [],
        reference_script: question.reference_script ?? '',
        previous_session_id: previousSessionId,
        transcript: content,
        duration_sec: elapsed || Math.max(1, Math.round(content.length / 3.2)),
      })
      setSession(result)
      setRetryMissedPoints(
        (result.point_review?.missed_points ?? []).map((item) => item.text),
      )
      await refreshProgress()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '分析失败。')
    } finally {
      setBusy('')
    }
  }

  const goalDone = Math.min(progress?.today_count ?? 0, progress?.daily_goal ?? 3)

  return (
    <div className="space-y-4">
      <section className="tool-card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
            <Mic className="size-5 text-emerald-600" aria-hidden="true" />
            表达训练
          </h2>
          <p className="text-xs text-zinc-500">
            一次一题，只练怎么说，不考知识点。每天 {progress?.daily_goal ?? 3} 题，紧张和口头禅会随趋势一起改善。
          </p>
        </div>

        {isRetrying && retryMissedPoints.length > 0 && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3">
            <div className="flex items-center gap-2">
              <Redo2 className="size-4 text-red-600" aria-hidden="true" />
              <p className="text-sm font-medium text-red-800">遗漏重述训练</p>
            </div>
            <p className="mt-1.5 text-xs leading-5 text-red-700">
              本轮只针对上一次没讲清的要点，强制使用无提示模式。把它们自然地补进回答里。
            </p>
            <ul className="mt-2 space-y-1 text-sm leading-6 text-red-900">
              {retryMissedPoints.map((point, index) => (
                <li key={`${point}-${index}`}>· {point}</li>
              ))}
            </ul>
          </div>
        )}

        {progress && progress.total_sessions > 0 && (
          <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="flex items-center justify-center gap-1 text-xs text-zinc-500">
                <Flame className="size-3.5 text-orange-500" aria-hidden="true" />
                连续练习
              </dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">{progress.streak_days} 天</dd>
            </div>
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="text-xs text-zinc-500">今日进度</dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">
                {goalDone}/{progress.daily_goal}
              </dd>
            </div>
            <div className="rounded-lg bg-zinc-50 px-2 py-2.5">
              <dt className="flex items-center justify-center gap-1 text-xs text-zinc-500">
                <TrendingUp className="size-3.5 text-emerald-600" aria-hidden="true" />
                累计
              </dt>
              <dd className="mt-0.5 text-lg font-semibold text-zinc-900">{progress.total_sessions} 题</dd>
            </div>
          </dl>
        )}

        {progress && progress.total_sessions > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-500">
            <span className="rounded-full bg-zinc-50 px-2.5 py-1">
              照读 {progress.mode_counts?.read ?? 0} 次
            </span>
            <span className="rounded-full bg-zinc-50 px-2.5 py-1">
              关键词 {progress.mode_counts?.keywords ?? 0} 次
            </span>
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">
              无提示 {progress.blind_total ?? 0} 次 · 均分 {progress.blind_average || '--'}
            </span>
          </div>
        )}

        <div className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <span className="field-label">练习来源</span>
              <div className="mt-2 flex flex-wrap gap-2">
                {EXPRESSION_SOURCES.map((source) => {
                  const active = sourceType === source.key
                  return (
                    <button
                      key={source.key}
                      type="button"
                      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                        active
                          ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                          : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                      }`}
                      onClick={() => changeSourceType(source.key)}
                      disabled={Boolean(busy)}
                    >
                      {source.label}
                    </button>
                  )
                })}
              </div>
              <p className="mt-1.5 text-xs leading-5 text-zinc-500">
                {EXPRESSION_SOURCES.find((source) => source.key === sourceType)?.hint}
              </p>
            </div>
            <div>
              <span className="field-label">练习题型</span>
              <div className="mt-2 flex flex-wrap gap-2">
                {QUESTION_TYPE_OPTIONS.map((option) => {
                  const active = questionType === option.key
                  return (
                    <button
                      key={option.key || 'any'}
                      type="button"
                      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                        active
                          ? 'border-sky-500 bg-sky-50 text-sky-800'
                          : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                      }`}
                      onClick={() => changeQuestionType(option.key)}
                      disabled={Boolean(busy)}
                    >
                      {option.label}
                    </button>
                  )
                })}
              </div>
              <p className="mt-1.5 text-xs leading-5 text-zinc-500">
                {QUESTION_TYPE_OPTIONS.find((option) => option.key === questionType)?.hint}
                题型约束主要作用于知识库文档和简历生成题；真题库仍以现有抽题为准。
              </p>
            </div>
          </div>

          {sourceType === 'knowledge' && (
            <div>
              <p className="text-xs font-medium text-zinc-600">指定知识库文档（可多选）</p>
              {documents.length === 0 ? (
                <p className="mt-1.5 text-xs leading-5 text-zinc-500">
                  知识库还没有文档，请先在「在线刷题」页右侧导入面试错题本或面试纪要。
                </p>
              ) : (
                <div className="mt-2 max-h-44 space-y-1 overflow-y-auto rounded-lg border border-zinc-200 bg-white p-2">
                  {documents.map((doc) => (
                    <label
                      key={doc.doc_id}
                      className="flex items-start gap-2 rounded-md px-2 py-1.5 text-sm leading-5 text-zinc-700 hover:bg-zinc-50"
                    >
                      <input
                        type="checkbox"
                        className="mt-1 size-4 accent-emerald-600"
                        checked={selectedDocIds.includes(doc.doc_id)}
                        onChange={() => toggleDocument(doc.doc_id)}
                      />
                      <span className="min-w-0 truncate">{doc.title}</span>
                    </label>
                  ))}
                </div>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2.5">
                <span className="text-xs font-medium text-emerald-900">
                  已选 {selectedDocIds.length} 份文档
                </span>
                <button
                  type="button"
                  className="primary-btn"
                  onClick={generateKnowledgeQuestion}
                  disabled={
                    selectedDocIds.length === 0 ||
                    Boolean(busy) ||
                    knowledgeGroupFull
                  }
                >
                  {busy === 'question' ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <ListChecks className="size-4" aria-hidden="true" />
                  )}
                  {knowledgeGroupFull
                    ? '本组已满 5 道'
                    : busy === 'question'
                      ? '正在生成'
                      : '生成 1 道表达题'}
                </button>
                {knowledgePool.questions.length > 0 && (
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={replayKnowledgePool}
                    disabled={Boolean(busy)}
                  >
                    重练本组
                  </button>
                )}
                <span className="text-xs text-zinc-500">
                  本组已生成 {knowledgePool.questions.length}/
                  {KNOWLEDGE_POOL_LIMIT} 道
                </span>
              </div>
              {knowledgeSelectionDirty && (
                <p className="mt-1 text-xs leading-5 text-amber-700">
                  勾选或题型已变更，点「生成 1 道表达题」后生效。
                </p>
              )}
              {knowledgePool.exhausted && (
                <p className="mt-1 text-xs leading-5 text-amber-700">
                  {knowledgePool.message || '本组已练完，换文档或题型开始新一组。'}
                </p>
              )}
            </div>
          )}

          {sourceType === 'mistake' && (
            <div>
              <div className="flex items-center justify-between gap-3">
                <span className="field-label">选择知识点</span>
                {selectedReviewGroup && (
                  <button
                    type="button"
                    className="text-xs font-semibold text-emerald-700 underline underline-offset-2"
                    onClick={() => setReviewPickerOpen((open) => !open)}
                    disabled={Boolean(busy)}
                  >
                    {reviewPickerOpen ? '收起列表' : '更换知识点'}
                  </button>
                )}
              </div>

              {selectedReviewGroup ? (
                <div className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2.5">
                  <p className="truncate text-xs font-semibold text-emerald-800">
                    {selectedReviewGroup.topic}
                  </p>
                  {selectedReviewGroup.records.length > 1 ? (
                    <p className="mt-1 text-sm leading-6 text-zinc-700">
                      {selectedReviewGroup.records.length} 道错题已合并，训练时会改写成一道问答题。
                    </p>
                  ) : (
                    <p className="mt-1 line-clamp-2 text-sm leading-6 text-zinc-800">
                      {selectedReviewGroup.representative.stem}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-zinc-500">
                    {selectedReviewGroup.records.length > 1
                      ? `该知识点共 ${selectedReviewGroup.records.length} 道错题，合并成 1 道问答题`
                      : `错 ${selectedReviewGroup.representative.wrong_count} 次`}
                    {' · '}
                    {formatReviewAge(selectedReviewGroup.latestAt)}
                  </p>
                </div>
              ) : (
                <p className="mt-1.5 text-xs leading-5 text-zinc-500">
                  {reviewRecords.length > 0
                    ? '在下面的列表里搜索并选择一个知识点，同知识点的多道错题会合并成一道问答题。'
                    : '还没有错题复盘记录，先在「在线刷题」完成答卷并同步错题。'}
                </p>
              )}

              {reviewPickerOpen && reviewRecords.length > 0 && (
                <div className="mt-2 rounded-lg border border-zinc-200 bg-white p-2.5">
                  <div className="relative">
                    <Search
                      className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-400"
                      aria-hidden="true"
                    />
                    <input
                      type="search"
                      className="text-input pl-9"
                      value={reviewSearch}
                      onChange={(event) => setReviewSearch(event.target.value)}
                      placeholder="搜索知识点、题干、选项或来源文档"
                      aria-label="搜索错题"
                      disabled={Boolean(busy)}
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {REVIEW_RANGES.map((range) => {
                      const active = reviewRange === range.key
                      return (
                        <button
                          key={range.key}
                          type="button"
                          className={`rounded-md border px-2.5 py-1 text-xs font-medium transition ${
                            active
                              ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                              : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                          }`}
                          onClick={() => setReviewRange(range.key)}
                          aria-pressed={active}
                          disabled={Boolean(busy)}
                        >
                          {range.label}
                        </button>
                      )
                    })}
                  </div>
                  <p className="mt-2 text-xs text-zinc-500">
                    共 {reviewGroups.length} 个知识点（{reviewRecords.length} 道错题），当前匹配{' '}
                    {filteredReviewGroups.length} 个知识点
                  </p>
                  <div className="mt-1.5 max-h-64 space-y-1 overflow-y-auto">
                    {filteredReviewGroups.length === 0 ? (
                      <p className="rounded-md bg-zinc-50 px-3 py-4 text-center text-xs leading-5 text-zinc-500">
                        没有匹配的错题，试试放宽时间范围或换个关键词。
                      </p>
                    ) : (
                      filteredReviewGroups.map((group) => {
                        const active = group.records.some(
                          (record) => record.review_id === selectedReviewId,
                        )
                        return (
                          <button
                            key={group.key}
                            type="button"
                            className={`flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left transition ${
                              active
                                ? 'border-emerald-400 bg-emerald-50'
                                : 'border-transparent hover:border-zinc-200 hover:bg-zinc-50'
                            }`}
                            onClick={() => changeReviewRecord(group.representative.review_id)}
                            aria-pressed={active}
                            disabled={Boolean(busy)}
                          >
                            <span className="min-w-0 flex-1">
                              <span className="flex items-center gap-2">
                                <span className="truncate text-xs font-medium text-zinc-500">
                                  {group.topic}
                                </span>
                                <span className="shrink-0 text-[11px] text-zinc-400">
                                  {group.records.length > 1
                                    ? `${group.records.length} 道错题`
                                    : `错 ${group.representative.wrong_count} 次`}
                                </span>
                              </span>
                              <span className="mt-0.5 line-clamp-2 block text-sm leading-5 text-zinc-800">
                                {group.representative.stem}
                              </span>
                            </span>
                            <span className="mt-0.5 shrink-0 text-[11px] text-zinc-400">
                              {formatReviewAge(group.latestAt)}
                            </span>
                          </button>
                        )
                      })
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {sourceType === 'resume' && (
            <div className="rounded-lg bg-zinc-50 px-3 py-2.5 text-xs leading-5 text-zinc-600">
              {resumeRecord
                ? `使用当前简历《${resumeRecord.title}》出题，聚焦项目、技能与亮点。`
                : '暂无简历，请先在「简历管理」上传或粘贴简历后再使用该题源。'}
            </div>
          )}
        </div>

        {sourceType !== 'knowledge' && (
          <div className="mt-4">
            <label className="field-label" htmlFor="expression-role">
              练习方向
            </label>
            <select
              id="expression-role"
              className="text-input disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-400"
              value={roleKey}
              onChange={(event) => chooseRole(event.target.value)}
              disabled={roles.length === 0 || Boolean(busy)}
            >
              {roles.length === 0 ? (
                <option value="">岗位加载中...</option>
              ) : (
                roles.map((role) => (
                  <option key={role.key} value={role.key}>
                    {role.name}
                  </option>
                ))
              )}
            </select>
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              {roles.find((role) => role.key === roleKey)?.summary ?? '正在加载练习方向。'}
            </p>
          </div>
        )}
      </section>

      <section className="tool-card">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-zinc-500">
              {question
                ? question.source_type === 'mistake'
                  ? `知识点表达训练 ${question.label || ''}`
                  : question.source_type === 'knowledge'
                    ? `知识点表达训练 ${question.label || ''}${
                        knowledgePool.questions.length > 0
                          ? ` · 第 ${knowledgePool.index + 1}/${knowledgePool.questions.length} 道`
                          : ''
                      }`
                    : `真题编号 ${question.label || '未编号'}`
                : sourceType === 'knowledge'
                  ? knowledgeGenerating
                    ? '正在生成表达题'
                    : '勾选文档后点「生成 1 道表达题」开始'
                  : sourceType === 'resume'
                    ? resumeGenerating
                      ? '正在生成表达题'
                      : '点击右侧按钮，从当前简历生成一道表达题'
                    : '选好岗位后取一道真题开始'}
            </p>
            <p className="mt-1.5 text-[15px] font-medium leading-7 text-zinc-900">
              {question?.question ??
                (knowledgeGenerating
                  ? '正在从所选文档生成表达题，完成后会自动显示，不需要重复点击。'
                  : resumeGenerating
                    ? '正在从当前简历生成表达题，完成后会自动显示，不需要重复点击。'
                    : sourceType === 'knowledge'
                      ? '先在上方勾选知识库文档，再点「生成 1 道表达题」。'
                      : sourceType === 'resume'
                        ? '点击右侧按钮，基于当前简历的项目、技能和亮点生成一道表达题。'
                        : '点击右侧按钮，从你的真实面试题库里抽一道表达练习题。')}
            </p>
            {question?.source && (
              <p className="mt-1 text-xs leading-5 text-zinc-500">
                来源文档：{question.source}
              </p>
            )}
            {(question?.source_type === 'mistake' ||
              question?.source_tag === 'wrong_review') && (
              <p className="mt-1 text-xs font-medium text-emerald-700">
                {question.group_size && question.group_size > 1
                  ? `已合并「${question.group_topic || topicHint}」下 ${question.group_size} 道错题`
                  : '来自错题复盘，已改写为问答题'}
              </p>
            )}
            {question?.source_tag === 'result_page' && (
              <p className="mt-1 text-xs font-medium text-sky-700">来自阅卷结果页</p>
            )}
            {question?.source_tag === 'quiz_dashboard' && (
              <p className="mt-1 text-xs font-medium text-zinc-600">来自在线刷题</p>
            )}
            {question?.source_type !== 'knowledge' &&
              question?.topic_matched === false && (
              <p className="mt-1 text-xs leading-5 text-amber-700">
                真题库里暂未找到完全匹配的题，已选择一道最接近该知识点的真题。
              </p>
              )}
            {question?.disclaimer && (
              <p className="mt-1 text-xs leading-5 text-amber-700">
                {question.disclaimer}
              </p>
            )}
            {question?.generation_pending && (
              <p className="mt-1 text-xs font-medium text-sky-700">
                AI 改写正在后台生成，完成后会自动刷新。
              </p>
            )}
          </div>
          {question?.source_type === 'mistake' ? (
            <button
              type="button"
              className="secondary-btn shrink-0"
              onClick={() => {
                setReviewPickerOpen(true)
                window.scrollTo({ top: 0, behavior: 'smooth' })
              }}
              disabled={Boolean(busy)}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              换知识点
            </button>
          ) : (
            <button
              type="button"
              className="secondary-btn shrink-0"
              onClick={() => {
                if (question?.source_type === 'knowledge') {
                  nextKnowledgeQuestion()
                  return
                }
                void loadQuestion(roleKey, topicHint)
              }}
              disabled={
                Boolean(busy) ||
                (question?.source_type === 'knowledge' &&
                  knowledgePoolAtEnd &&
                  knowledgePool.exhausted)
              }
            >
              {busy === 'question' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="size-4" aria-hidden="true" />
              )}
              {question?.source_type === 'knowledge' &&
              knowledgePoolAtEnd &&
              knowledgePool.exhausted
                ? '本组已练完'
                : question
                  ? '换一题'
                  : '生成一题'}
            </button>
          )}
        </div>

        {knowledgeGenerating && sourceType === 'knowledge' && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-800">
            <Loader2
              className="mt-0.5 size-3.5 shrink-0 animate-spin"
              aria-hidden="true"
            />
            <span>
              正在从所选文档生成表达题。当前模型较慢时可能需要 1–2
              分钟，页面会自动等待，不需要重复点击。
            </span>
          </div>
        )}

        {resumeGenerating && sourceType === 'resume' && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-800">
            <Loader2
              className="mt-0.5 size-3.5 shrink-0 animate-spin"
              aria-hidden="true"
            />
            <span>
              正在从当前简历生成表达题。当前模型较慢时可能需要 1–2
              分钟，页面会自动等待，不需要重复点击。
            </span>
          </div>
        )}

        {topicHint && (
          <p
            className={`mt-2 rounded-lg border px-3 py-2 text-xs leading-5 ${
              question?.source_type !== 'knowledge' &&
              question?.topic_matched === false
                ? 'border-amber-200 bg-amber-50 text-amber-800'
                : 'border-sky-100 bg-sky-50 text-sky-800'
            }`}
          >
            {busy === 'question'
              ? question?.source_type === 'mistake'
                ? `正在把「${topicHint}」下的错题改写成问答题。`
                : `正在根据薄弱知识点「${topicHint}」选题。`
              : question?.source_type === 'mistake'
                ? `已绑定知识点：${question.group_topic || topicHint}${
                    question.group_size && question.group_size > 1
                      ? `，${question.group_size} 道错题已合并为一道问答题`
                      : ''
                  }。`
                : question?.source_type === 'knowledge'
                  ? `已根据所选知识库文档生成题目。`
                  : question?.topic_matched === false
                    ? `真题库里暂时没有匹配「${topicHint}」的题，已选择最接近的一道真题。练习方向仍按薄弱知识点定位；导入相关真题库后会优先命中。`
                    : `已定位薄弱知识点：${topicHint}。本题来自相关真题。`}
          </p>
        )}

        <div className="mt-4 grid grid-cols-3 gap-2">
          {HINT_MODES.map((mode) => {
            const HintIcon = mode.icon
            const active = hintMode === mode.key
            const lockedByRetry = isRetrying && mode.key !== 'blind'
            return (
              <button
                key={mode.key}
                type="button"
                onClick={() => chooseHintMode(mode.key)}
                disabled={lockedByRetry || busy === 'record' || busy === 'transcribing' || busy === 'analyze'}
                className={`flex items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-xs font-medium transition ${
                  active
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-800'
                    : lockedByRetry
                      ? 'border-zinc-100 bg-zinc-50 text-zinc-300'
                      : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300'
                }`}
              >
                <HintIcon className="size-3.5" aria-hidden="true" />
                {mode.label}
              </button>
            )
          })}
        </div>
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          {HINT_MODES.find((mode) => mode.key === hintMode)?.description}
        </p>

        {question && hintMode === 'read' && (
          <div className="mt-3 rounded-lg bg-emerald-50 p-3">
            <p className="text-xs font-semibold text-emerald-900">
              题库参考答案 · 照着读，先找语感
            </p>
            {question.reference_answer ? (
              <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-line pr-1 text-sm leading-7 text-emerald-950">
                {question.reference_answer}
              </p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-amber-800">
                这道题没有抽取到参考答案，建议先切换到关键词或无提示模式。
              </p>
            )}
          </div>
        )}

        {question && hintMode === 'keywords' && (
          <div className="mt-3 rounded-lg bg-zinc-50 p-3">
            <p className="text-xs font-semibold text-zinc-700">只看关键词，自己串成完整回答</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {question.keywords.length > 0 ? (
                question.keywords.map((keyword) => (
                  <span
                    key={keyword}
                    className="rounded-full border border-zinc-200 bg-white px-2.5 py-1 text-xs text-zinc-700"
                  >
                    {keyword}
                  </span>
                ))
              ) : (
                <span className="text-xs text-zinc-500">暂无关键词，请直接按题干组织答案。</span>
              )}
            </div>
          </div>
        )}

        {question && hintMode === 'blind' && (
          <div className="mt-3 rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-xs leading-5 text-zinc-600">
            无提示模式：不展示答案和关键词。先停半秒给结论，再展开两三个要点，最后一句收束。
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {busy !== 'record' ? (
            <button
              type="button"
              className="primary-btn"
              onClick={startAnswer}
              disabled={!question || !mediaSupported || Boolean(busy)}
            >
              {busy === 'transcribing' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Mic className="size-4" aria-hidden="true" />
              )}
              {busy === 'transcribing' ? '本地识别中' : '开始口述'}
            </button>
          ) : (
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-700 transition hover:bg-red-100"
              onClick={stopAnswer}
            >
              <Square className="size-4" aria-hidden="true" />
              结束口述
            </button>
          )}
          <span
            className={`font-mono text-sm ${
              busy === 'record'
                ? 'text-red-600'
                : busy === 'transcribing'
                  ? 'text-amber-600'
                  : 'text-zinc-500'
            }`}
          >
            {formatClock(elapsed)}
          </span>
          {busy === 'record' && <span className="text-xs text-zinc-500">正在录音，说完点「结束口述」</span>}
          {busy === 'transcribing' && (
            <span className="text-xs text-amber-700">本地 Whisper 转写中，会尽量保留语气词和卡顿。</span>
          )}
          {!mediaSupported && (
            <span className="text-xs text-amber-700">当前浏览器不支持录音，可直接打字或粘贴转写作答。</span>
          )}
        </div>

        <label htmlFor="expression-transcript" className="field-label mt-4 block">
          回答内容（识别结果可直接修改）
        </label>
        <textarea
          id="expression-transcript"
          className="text-input mt-1 min-h-32 resize-y leading-7"
          value={transcript}
          onChange={(event) => setTranscript(event.target.value)}
          disabled={busy === 'record' || busy === 'transcribing'}
          placeholder="点「开始口述」直接说，或在这里把你的回答打出来。目标 60 到 90 秒：先给结论，再讲两三点，最后一句收束。"
        />
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          本地 Whisper 按逐字稿转写；提交前可快速校对，评分以文本框中的最终内容和真实音频时长为准。
        </p>

        {error && (
          <p className="mt-3 flex items-start gap-2 text-sm leading-5 text-red-600">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </p>
        )}

        <button
          type="button"
          className="primary-btn mt-4 w-full py-3"
          onClick={() => void submit()}
          disabled={Boolean(busy) || transcript.trim().length < 10}
        >
          {busy === 'analyze' ? (
            <Loader2 className="size-5 animate-spin" aria-hidden="true" />
          ) : (
            <TrendingUp className="size-5" aria-hidden="true" />
          )}
          {busy === 'analyze' ? '正在分析表达' : '提交，看这一轮的表达反馈'}
        </button>
      </section>

      {session && (
        <section className="tool-card">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-zinc-900">本轮反馈</h2>
              <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">
                {MODE_LABEL[session.practice_mode]}
              </span>
              {session.attempt_no && session.attempt_no > 1 && (
                <span className="rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-700">
                  第 {session.attempt_no} 次尝试
                </span>
              )}
            </div>
            <button
              type="button"
              className="secondary-btn"
              onClick={() => void loadQuestion(roleKey, topicHint)}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              练下一题
            </button>
          </div>

          <ExpressionPointReviewCard
            pointReview={session.point_review}
            onRetry={retryMissedPointsAgain}
          />

          {session.practice_mode === 'blind' && session.reference_script && (
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3">
                <p className="text-xs font-semibold text-zinc-700">你的转写</p>
                <p className="mt-2 max-h-60 overflow-y-auto whitespace-pre-line pr-1 text-sm leading-6 text-zinc-800">
                  {session.transcript}
                </p>
              </div>
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                <p className="text-xs font-semibold text-emerald-900">参考话术（提交后展示）</p>
                <p className="mt-2 max-h-60 overflow-y-auto whitespace-pre-line pr-1 text-sm leading-6 text-emerald-950">
                  {session.reference_script}
                </p>
              </div>
            </div>
          )}

          <div
            className={`mt-3 rounded-lg border px-3 py-2.5 ${
              session.progression.passed
                ? 'border-emerald-200 bg-emerald-50'
                : 'border-amber-200 bg-amber-50'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p
                className={`text-sm font-medium ${
                  session.progression.passed ? 'text-emerald-900' : 'text-amber-900'
                }`}
              >
                {session.progression.passed ? '本档通过' : '建议继续巩固'}
              </p>
              <button
                type="button"
                className={session.progression.passed ? 'primary-btn px-3 py-1.5 text-xs' : 'secondary-btn px-3 py-1.5 text-xs'}
                onClick={handleProgressionClick}
              >
                {session.progression.next_mode === 'next_question'
                  ? '通过，换一道新题'
                  : session.progression.next_mode === hintMode
                    ? '同一题再练一次'
                    : `下一阶：${MODE_LABEL[session.progression.next_mode]}`}
              </button>
            </div>
            <p
              className={`mt-1.5 text-sm leading-6 ${
                session.progression.passed ? 'text-emerald-800' : 'text-amber-800'
              }`}
            >
              {session.progression.message}
            </p>
          </div>

          <ExpressionComparisonCard comparison={session.comparison} />

          <div className="mt-4 grid grid-cols-3 gap-3">
            {(['fluency', 'structure', 'confidence'] as const).map((key) => (
              <div key={key}>
                <div className="flex items-center justify-between text-xs text-zinc-500">
                  <span>{SCORE_LABEL[key]}</span>
                  <span className="font-semibold text-zinc-800">{session.metrics.scores[key]}</span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-lg bg-zinc-200">
                  <div
                    className={`h-full rounded-lg ${
                      session.metrics.scores[key] >= 80
                        ? 'bg-emerald-600'
                        : session.metrics.scores[key] >= 60
                          ? 'bg-amber-500'
                          : 'bg-red-500'
                    }`}
                    style={{ width: `${session.metrics.scores[key]}%` }}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              语速 {session.metrics.rate_cpm} 字/分
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              语气词 {session.metrics.filler_total} 个
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              口头禅 {session.metrics.crutch_total} 次
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600">
              卡顿重复 {session.metrics.restart_count} 处
            </span>
            {session.metrics.structure_markers.length > 0 && (
              <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">
                结构词 {session.metrics.structure_markers.join('、')}
              </span>
            )}
          </div>

          <p className="mt-4 rounded-lg bg-emerald-50 px-3 py-2.5 text-sm leading-6 text-emerald-900">
            {session.coach.summary}
          </p>

          {session.coach.strengths.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-500">这轮做得好的</p>
              <ul className="mt-1 space-y-1 text-sm leading-6 text-zinc-700">
                {session.coach.strengths.map((item, index) => (
                  <li key={index}>· {item}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3">
            <p className="text-xs font-semibold text-zinc-500">怎么改</p>
            <ul className="mt-1 space-y-1 text-sm leading-6 text-zinc-700">
              {session.coach.fixes.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>

          {session.coach.example && (
            <div className="mt-3">
              <p className="text-xs font-semibold text-zinc-500">一句话改写示范</p>
              <p className="mt-1 rounded-lg bg-zinc-50 px-3 py-2 text-sm leading-6 text-zinc-800">
                {session.coach.example}
              </p>
            </div>
          )}

          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {session.coach.next_focus && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
                下轮重点：{session.coach.next_focus}
              </p>
            )}
            {session.coach.mindset_tip && (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm leading-6 text-emerald-900">
                临场心态：{session.coach.mindset_tip}
              </p>
            )}
          </div>
        </section>
      )}

      {progress && progress.trend.length >= 2 && (
        <section className="tool-card">
          <h2 className="text-base font-semibold text-zinc-900">
            最近 {progress.trend.length} 次趋势（照读/关键词/无提示分开看）
          </h2>
          <div className="mt-3 space-y-1.5">
            {progress.trend.slice(-10).map((item, index) => (
              <div key={index} className="flex items-center gap-2 text-xs text-zinc-500">
                <span className="w-20 shrink-0 truncate text-[10px] leading-4">
                  {new Date(item.created_at * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
                  <span className="ml-1 text-zinc-400">{MODE_LABEL[item.practice_mode ?? 'blind'].slice(0, 2)}</span>
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-lg bg-zinc-100">
                  <div
                    className="h-full rounded-lg bg-emerald-500"
                    style={{ width: `${item.fluency}%` }}
                  />
                </div>
                <span className="w-8 shrink-0 text-right font-medium text-zinc-700">{item.fluency}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
