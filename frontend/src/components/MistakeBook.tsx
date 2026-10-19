import { ArrowRight, BookMarked, BookOpen, Loader2, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { generateQuizPaper } from '../api'
import type {
  QuizPaper,
  QuizProgress,
  RecentWeakTopic,
  TopicProgress,
} from '../types'

interface MistakeBookProps {
  progress: QuizProgress | null
  reviewDueCount: number
  onOpenReview: () => void
  onPaperReady: (paper: QuizPaper) => void
}

const TYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

const moduleLabel = (module: string) =>
  module === '综合测试能力' ? '传统软件测试' : module || '待分类'

const weaknessScore = (topic: TopicProgress) => {
  const lastAnswerCorrect = topic.streak === 0 ? 0 : 1
  return topic.accuracy * 0.65 + lastAnswerCorrect * 0.35
}

function getTopicHint(topic: TopicProgress): string {
  if (topic.total < 4) {
    return `样本还少，再练 ${4 - topic.total} 道后判断是否稳定。`
  }
  if (topic.accuracy < 0.6) return '正确率偏低，先回读资料和错题解析。'
  const missingTypes = (['single', 'multiple', 'judge'] as const).filter(
    (type) => (topic.by_type?.[type]?.correct ?? 0) === 0,
  )
  if (!topic.cross_type_verified && missingTypes.length > 0) {
    const labels = missingTypes.slice(0, 2).map((type) => TYPE_LABEL[type]).join('、')
    return `还需要通过 ${labels} 换题型验证。`
  }
  if (topic.accuracy < 0.85) return '正确率未到 85%，继续用混合场景巩固。'
  if (topic.streak < 2) return '最近连续答对不足 2 次，再做一组确认稳定性。'
  if (topic.total >= 4 && topic.accuracy >= 0.85 && topic.cross_type_verified) {
    return '已跨题型掌握，后续间隔复习即可。'
  }
  return '继续保持混合题型练习。'
}

function getRecentTopicHint(topic: RecentWeakTopic): string {
  if (topic.sample_state === 'tentative') {
    const remaining = Math.max(1, 3 - topic.total)
    return `样本 ${topic.total} 题，补 ${remaining} 题再判断。`
  }
  const wrongTypes = (['single', 'multiple', 'judge'] as const)
    .map((type) => ({
      type,
      ...(topic.by_type?.[type] ?? { total: 0, correct: 0, accuracy: 1 }),
    }))
    .filter((item) => item.total > 0 && item.correct < item.total)
    .sort((left, right) => left.accuracy - right.accuracy)
  if (wrongTypes.length > 0) {
    const labels = wrongTypes
      .slice(0, 2)
      .map((item) => TYPE_LABEL[item.type])
      .join('、')
    return `${labels}有错，先复盘再巩固。`
  }
  return `近 7 天 ${topic.correct}/${topic.total}，继续混合题型巩固。`
}

function recentSourceLabel(topic: RecentWeakTopic): string {
  const first = topic.source_docs?.[0]
  if (!first?.title) return ''
  if (topic.source_count > 1) return `${first.title} 等 ${topic.source_count} 篇`
  return first.title
}

function recentSourceTitle(topic: RecentWeakTopic): string {
  return (topic.source_docs ?? [])
    .map((doc) => doc.title)
    .filter(Boolean)
    .join('、')
}

interface WeakTopicCardProps {
  topic: string
  correct: number
  total: number
  accuracy: number
  hint: string
  hintTitle?: string
  sourceLabel?: string
  sourceTitle?: string
  tentative?: boolean
  practiceBusy: string
  onPractice: () => void
}

function WeakTopicCard({
  topic,
  correct,
  total,
  accuracy,
  hint,
  hintTitle,
  sourceLabel,
  sourceTitle,
  tentative = false,
  practiceBusy,
  onPractice,
}: WeakTopicCardProps) {
  const active = practiceBusy === `topic:${topic}`
  return (
    <li className="rounded-lg bg-zinc-50 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2 text-xs">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-medium text-zinc-800">{topic}</span>
          {tentative && (
            <span className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold leading-none text-amber-700">
              待验证
            </span>
          )}
        </div>
        <span className="shrink-0 text-zinc-500">
          {correct}/{total}
        </span>
      </div>
      {sourceLabel && (
        <p
          className="mt-1 flex items-center gap-1 text-[10px] leading-4 text-zinc-400"
          title={sourceTitle || sourceLabel}
        >
          <BookOpen className="size-3 shrink-0" aria-hidden="true" />
          <span className="min-w-0 truncate">来源：{sourceLabel}</span>
        </p>
      )}
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-lg bg-zinc-200">
        <div
          className={`h-full rounded-lg ${
            accuracy >= 0.85
              ? 'bg-emerald-600'
              : accuracy >= 0.6
                ? 'bg-amber-500'
                : 'bg-red-500'
          }`}
          style={{ width: `${Math.max(4, accuracy * 100)}%` }}
        />
      </div>
      <p
        className="mt-1 line-clamp-2 text-[11px] leading-4 text-zinc-500"
        title={hintTitle || undefined}
      >
        {hint}
      </p>
      <button
        type="button"
        className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-emerald-600 px-1.5 py-0.5 text-[11px] font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
        onClick={onPractice}
        disabled={Boolean(practiceBusy)}
      >
        {active ? (
          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
        ) : (
          <RotateCcw className="size-3" aria-hidden="true" />
        )}
        巩固 5 题
      </button>
    </li>
  )
}

export function MistakeBook({
  progress,
  reviewDueCount,
  onOpenReview,
  onPaperReady,
}: MistakeBookProps) {
  const [practiceBusy, setPracticeBusy] = useState('')
  const [practiceError, setPracticeError] = useState('')
  const fallbackTopics = (progress?.topics ?? [])
    .slice()
    .sort(
      (left, right) =>
        weaknessScore(left) - weaknessScore(right) ||
        (right.updated_at ?? 0) - (left.updated_at ?? 0) ||
        right.total - left.total,
    )
    .slice(0, 5)
  const hasRecentBoard = Array.isArray(progress?.recent_weak_topics)
  const recentTopics = progress?.recent_weak_topics ?? []
  const recentWindow = progress?.recent_window
  const hasData = Boolean(progress && progress.answered_total > 0)

  const moduleTopics = (module: string): TopicProgress[] =>
    (progress?.topics ?? []).filter(
      (item) => moduleLabel(item.module || '') === module,
    )

  const handlePracticeTopics = async (label: string, keywords: string[]) => {
    if (practiceBusy || keywords.length === 0) return
    setPracticeBusy(label)
    setPracticeError('')
    try {
      const paper = await generateQuizPaper({
        keywords,
        single: label.startsWith('topic:') ? 2 : 4,
        multiple: label.startsWith('topic:') ? 1 : 3,
        judge: label.startsWith('topic:') ? 2 : 3,
        difficulty: 'mixed',
        focus_weak: true,
        mode: 'weak_board',
        direction_id: label.startsWith('module:') ? label.slice('module:'.length) : '',
        knowledge_point_ids: label.startsWith('topic:')
          ? keywords
          : [],
      })
      onPaperReady(paper)
    } catch (error) {
      setPracticeError(error instanceof Error ? error.message : '巩固组卷失败。')
    } finally {
      setPracticeBusy('')
    }
  }

  return (
    <section className="tool-card">
      <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
        <BookMarked className="size-5 text-emerald-600" aria-hidden="true" />
        错题复盘入口
      </h2>

      <button
        type="button"
        className="mt-4 w-full rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-left transition hover:border-emerald-300 hover:bg-emerald-100"
        onClick={onOpenReview}
      >
        <span className="flex items-center justify-between gap-3">
          <span>
            <span className="block text-sm font-semibold text-emerald-900">今日待复盘</span>
            <span className="mt-1 block text-xs leading-5 text-emerald-800">
              {hasData
                ? `${reviewDueCount} 道到期 · ${progress?.mistake_count ?? 0} 道在错题本`
                : '交卷后错题会自动进入间隔复习'}
            </span>
          </span>
          <ArrowRight className="size-4 shrink-0 text-emerald-700" aria-hidden="true" />
        </span>
      </button>

      {hasData && (
        <button
          type="button"
          className="secondary-btn mt-2 w-full py-2 text-xs"
          onClick={onOpenReview}
        >
          查看完整知识地图和全部错题
        </button>
      )}

      {practiceError && (
        <p className="mt-2 text-xs leading-5 text-red-600">{practiceError}</p>
      )}

      {progress?.module_stats && progress.module_stats.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold text-zinc-700">板块掌握度</p>
          <ul className="space-y-2">
            {progress.module_stats.slice(0, 5).map((module) => (
              <li key={module.module}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate text-zinc-700">{moduleLabel(module.module)}</span>
                  <span className="shrink-0 text-zinc-500">
                    {Math.round(module.accuracy * 100)}%
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-lg bg-zinc-200">
                    <div
                      className={`h-full rounded-lg ${
                        module.accuracy >= 0.85
                          ? 'bg-emerald-600'
                          : module.accuracy >= 0.6
                            ? 'bg-amber-500'
                            : 'bg-red-500'
                      }`}
                      style={{ width: `${Math.max(4, module.accuracy * 100)}%` }}
                    />
                  </div>
                  <button
                    type="button"
                    className="inline-flex shrink-0 items-center gap-1 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:text-zinc-400"
                    onClick={() =>
                      void handlePracticeTopics(
                        `module:${module.module}`,
                        moduleTopics(module.module)
                          .slice(0, 6)
                          .map((topic) => topic.topic),
                      )
                    }
                    disabled={Boolean(practiceBusy) || moduleTopics(module.module).length === 0}
                  >
                    {practiceBusy === `module:${module.module}` ? (
                      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <RotateCcw className="size-3" aria-hidden="true" />
                    )}
                    巩固 10 题
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] leading-4 text-zinc-500">
            点击板块名旁的按钮，直接生成 10 题巩固卷。
          </p>
        </div>
      )}

      {(hasRecentBoard || fallbackTopics.length > 0) && (
        <div className="mt-4 border-t border-zinc-100 pt-3">
          <div className="mb-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700">
              最弱知识点 TOP5
              {hasRecentBoard && (
                <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-emerald-700">
                  近 7 天
                </span>
              )}
            </p>
            {hasRecentBoard && recentWindow && recentWindow.attempts > 0 && (
              <p className="mt-0.5 text-[10px] leading-4 text-zinc-400">
                {recentWindow.attempts} 份答卷 / {recentWindow.answered} 题
                {recentWindow.source_count > 0
                  ? ` · ${recentWindow.source_count} 份资料`
                  : ''}
              </p>
            )}
          </div>
          {hasRecentBoard ? (
            recentTopics.length > 0 ? (
              <ul className="space-y-2">
                {recentTopics.map((topic) => {
                  const diagnosis = topic.diagnosis?.trim()
                  return (
                    <WeakTopicCard
                      key={topic.topic}
                      topic={topic.topic}
                      correct={topic.correct}
                      total={topic.total}
                      accuracy={topic.accuracy}
                      hint={diagnosis || getRecentTopicHint(topic)}
                      hintTitle={diagnosis || undefined}
                      sourceLabel={recentSourceLabel(topic)}
                      sourceTitle={recentSourceTitle(topic)}
                      tentative={topic.sample_state === 'tentative'}
                      practiceBusy={practiceBusy}
                      onPractice={() =>
                        void handlePracticeTopics(`topic:${topic.topic}`, [topic.topic])
                      }
                    />
                  )
                })}
              </ul>
            ) : recentWindow?.attempts ? (
              <p className="rounded-lg border border-dashed border-zinc-200 bg-zinc-50 px-3 py-3 text-[11px] leading-5 text-zinc-500">
                近 7 天练习的知识点都达到了 80%，暂时没有需要优先巩固的薄弱点。
              </p>
            ) : (
              <p className="rounded-lg border border-dashed border-zinc-200 bg-zinc-50 px-3 py-3 text-[11px] leading-5 text-zinc-500">
                近 7 天还没有答题记录。完成一套题后，这里会自动更新。
              </p>
            )
          ) : (
            <ul className="space-y-2">
              {fallbackTopics.map((topic) => (
                <WeakTopicCard
                  key={topic.topic}
                  topic={topic.topic}
                  correct={topic.correct}
                  total={topic.total}
                  accuracy={topic.accuracy}
                  hint={getTopicHint(topic)}
                  practiceBusy={practiceBusy}
                  onPractice={() =>
                    void handlePracticeTopics(`topic:${topic.topic}`, [topic.topic])
                  }
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {!hasData && !hasRecentBoard && (
        <p className="mt-4 text-sm leading-6 text-zinc-500">
          完整知识点列表和错题记录在「错题复盘」页维护。
        </p>
      )}
    </section>
  )
}
