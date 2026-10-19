import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardPaste,
  FileUp,
  IdCard,
  Loader2,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  activateResume,
  deleteResume,
  getActiveResume,
  listResumes,
  pasteResume,
  uploadFile,
  uploadResumeFile,
} from '../api'
import type { ResumeRecord, ResumeSummary } from '../types'

interface ResumeManagerProps {
  activeResume: ResumeRecord | null
  resume: string
  onActiveResumeChange: (resume: ResumeRecord | null) => void
  onResumeChange: (value: string) => void
}

type ImportMode = 'file' | 'paste'

const RESUME_EXTENSIONS = ['.txt', '.md', '.markdown', '.pdf', '.docx', '.html', '.htm']
const MAX_RESUME_BYTES = 20 * 1024 * 1024

function isResumeFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return RESUME_EXTENSIONS.some((extension) => name.endsWith(extension))
}

function periodRange(value: string): { start: number; end: number } | null {
  const parts = (value.match(/\d+/g) ?? []).map(Number)
  if (parts.length < 2) return null
  const start = parts[0] * 12 + parts[1]
  const ongoing = /至今|现在|present|current|now/i.test(value)
  const today = new Date()
  const currentMonth = today.getFullYear() * 12 + today.getMonth() + 1
  const end =
    parts.length >= 4
      ? parts[2] * 12 + parts[3]
      : ongoing
        ? currentMonth
        : start
  return { start, end }
}

function periodOverlapMonths(leftValue: string, rightValue: string): number {
  const left = periodRange(leftValue)
  const right = periodRange(rightValue)
  if (!left || !right) return 0
  return Math.max(0, Math.min(left.end, right.end) - Math.max(left.start, right.start) + 1)
}

function assignProjectsToExperiences(
  experiences: ResumeRecord['profile']['experiences'],
  projects: ResumeRecord['profile']['projects'],
): Map<number, number> {
  const assignments = new Map<number, number>()
  projects.forEach((project, projectIndex) => {
    let bestExperience = -1
    let bestScore = 0
    experiences.forEach((experience, experienceIndex) => {
      const overlap = periodOverlapMonths(experience.period, project.period)
      if (overlap <= 0) return
      const roleMatches =
        !project.role || !experience.title || project.role === experience.title
      const score = overlap * 10 + (roleMatches ? 1 : 0)
      if (score > bestScore) {
        bestScore = score
        bestExperience = experienceIndex
      }
    })
    if (bestExperience >= 0) assignments.set(projectIndex, bestExperience)
  })
  return assignments
}

export function ResumeManager({
  activeResume,
  resume,
  onActiveResumeChange,
  onResumeChange,
}: ResumeManagerProps) {
  const [mode, setMode] = useState<ImportMode>('file')
  const [resumes, setResumes] = useState<ResumeSummary[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [fileTitle, setFileTitle] = useState('')
  const [pasteTitle, setPasteTitle] = useState('')
  const [pasteContent, setPasteContent] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [expandedExperiences, setExpandedExperiences] = useState<Set<string>>(
    () => new Set(),
  )
  const [showLibrary, setShowLibrary] = useState(false)
  const [showAllSkills, setShowAllSkills] = useState(false)
  const [showAllExperiences, setShowAllExperiences] = useState(false)
  const [showImport, setShowImport] = useState(false)

  const refreshList = async () => {
    try {
      setResumes(await listResumes())
    } catch {
      setResumes([])
    }
  }

  useEffect(() => {
    void refreshList()
  }, [activeResume?.resume_id])

  const chooseFile = (candidate: File | null) => {
    if (!candidate) return
    if (!isResumeFile(candidate)) {
      setError('简历请使用 PDF / DOCX / TXT / Markdown / HTML 格式。')
      return
    }
    if (candidate.size > MAX_RESUME_BYTES) {
      setError('简历文件超过 20MB，请精简后重试。')
      return
    }
    setError('')
    setFile(candidate)
    if (!fileTitle.trim()) {
      setFileTitle(candidate.name.replace(/\.[^.]+$/, ''))
    }
  }

  const handleFileImport = async () => {
    if (!file || busy) return
    setBusy('file')
    setError('')
    try {
      const uploaded = await uploadFile(file, () => undefined)
      const record = await uploadResumeFile(uploaded.file_id, fileTitle.trim())
      onActiveResumeChange(record)
      setFile(null)
      setFileTitle('')
      await refreshList()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '简历解析失败。')
    } finally {
      setBusy('')
    }
  }

  const handlePasteImport = async () => {
    if (!pasteContent.trim() || busy) return
    setBusy('paste')
    setError('')
    try {
      const record = await pasteResume(pasteTitle.trim() || '我的简历', pasteContent.trim())
      onActiveResumeChange(record)
      setPasteTitle('')
      setPasteContent('')
      await refreshList()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '简历解析失败。')
    } finally {
      setBusy('')
    }
  }

  const handleActivate = async (resumeId: string) => {
    if (busy) return
    setBusy(`activate:${resumeId}`)
    setError('')
    try {
      await activateResume(resumeId)
      onActiveResumeChange(await getActiveResume())
      await refreshList()
    } catch (activateError) {
      setError(activateError instanceof Error ? activateError.message : '切换简历失败。')
    } finally {
      setBusy('')
    }
  }

  const handleDelete = async (resumeId: string) => {
    if (busy) return
    setBusy(`delete:${resumeId}`)
    setError('')
    try {
      await deleteResume(resumeId)
      if (activeResume?.resume_id === resumeId) {
        onActiveResumeChange(await getActiveResume())
      }
      await refreshList()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除简历失败。')
    } finally {
      setBusy('')
    }
  }

  const profile = activeResume?.profile
  const otherResumes = resumes.filter(
    (item) => item.resume_id !== activeResume?.resume_id,
  )
  const visibleSkills = profile
    ? showAllSkills
      ? profile.skills
      : profile.skills.slice(0, 8)
    : []
  const visibleExperiences = profile
    ? showAllExperiences
      ? profile.experiences
      : profile.experiences.slice(0, 4)
    : []
  const projectExperienceMap = profile
    ? assignProjectsToExperiences(profile.experiences, profile.projects)
    : new Map<number, number>()
  const toggleAllExperiences = () => {
    if (!profile) return
    if (showAllExperiences) {
      setShowAllExperiences(false)
      setExpandedExperiences(new Set())
      return
    }
    setShowAllExperiences(true)
    setExpandedExperiences(new Set())
  }

  return (
    <section className="tool-card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <IdCard className="size-5 text-emerald-600" aria-hidden="true" />
          简历档案
        </h2>
        {profile && (
          <span className="flex shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs font-medium text-white">
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            使用中
          </span>
        )}
      </div>
      <p className="mt-1.5 text-xs leading-5 text-zinc-500">
        导入后自动抽取经历、技能和待深挖点，面试与评分以此为基础。
      </p>

      {profile && (
        <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-zinc-900">
              {profile.name || activeResume?.title || '我的简历'}
              {profile.years_of_experience > 0 && (
                <span className="ml-2 text-xs font-medium text-emerald-700">
                  {profile.years_of_experience} 年经验
                </span>
              )}
            </p>
            {profile.headline && (
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-600">
                {profile.headline}
              </p>
            )}
          </div>

          {profile.skills.length > 0 && (
            <>
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {visibleSkills.map((skill, index) => (
                  <li
                    key={`${skill}-${index}`}
                    className="rounded-lg bg-white px-2 py-1 text-xs text-zinc-700 ring-1 ring-emerald-200"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
              {profile.skills.length > 8 && (
                <button
                  type="button"
                  className="mt-1.5 text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
                  onClick={() => setShowAllSkills((current) => !current)}
                >
                  {showAllSkills
                    ? '收起关键词'
                    : `+${profile.skills.length - 8} 个关键词`}
                </button>
              )}
            </>
          )}

          {profile.experiences.length > 0 && (
            <div className="mt-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-zinc-700">工作经历</p>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 transition hover:text-emerald-800"
                  onClick={toggleAllExperiences}
                  aria-expanded={showAllExperiences}
                >
                  {showAllExperiences ? (
                    <ChevronDown className="size-3.5" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="size-3.5" aria-hidden="true" />
                  )}
                  {showAllExperiences ? '收起' : '展开全部'}
                </button>
              </div>
              <ul className="mt-1.5 space-y-1.5">
                {visibleExperiences.map((experience, index) => {
                  const key = `${experience.company}-${experience.period}`
                  const open = expandedExperiences.has(key)
                  const experienceIndex = profile.experiences.indexOf(experience)
                  const relatedProjects = profile.projects.filter(
                    (_project, projectIndex) =>
                      projectExperienceMap.get(projectIndex) === experienceIndex,
                  )

                  return (
                    <li
                      key={`${key}-${index}`}
                      className="overflow-hidden rounded-lg bg-white ring-1 ring-zinc-200"
                    >
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 px-3 py-2 text-left"
                        onClick={() =>
                          setExpandedExperiences((current) => {
                            const next = new Set(current)
                            if (next.has(key)) next.delete(key)
                            else next.add(key)
                            return next
                          })
                        }
                        aria-expanded={open}
                      >
                        {open ? (
                          <ChevronDown
                            className="size-4 shrink-0 text-zinc-400"
                            aria-hidden="true"
                          />
                        ) : (
                          <ChevronRight
                            className="size-4 shrink-0 text-zinc-400"
                            aria-hidden="true"
                          />
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-zinc-800">
                            {experience.company || `经历 ${index + 1}`}
                          </span>
                          <span className="block truncate text-xs text-zinc-500">
                            {[experience.title, experience.period].filter(Boolean).join(' · ')}
                          </span>
                        </span>
                        {relatedProjects.length > 0 && (
                          <span className="shrink-0 text-[11px] text-zinc-400">
                            {relatedProjects.length} 项
                          </span>
                        )}
                      </button>

                      {open && (
                        <div className="space-y-2 border-t border-zinc-100 px-3 py-2.5 text-xs leading-5 text-zinc-600">
                          {experience.summary && (
                            <p className="line-clamp-3">{experience.summary}</p>
                          )}
                          {relatedProjects.length > 0 && (
                            <div>
                              <p className="text-[11px] font-semibold text-zinc-500">
                                相关职责 / 项目（{relatedProjects.length}）
                              </p>
                              <ul className="mt-1 space-y-1">
                                {relatedProjects.map((project, projectIndex) => (
                                  <li
                                    key={`${project.name}-${projectIndex}`}
                                    className="flex gap-1.5"
                                  >
                                    <span className="mt-2 size-1 shrink-0 rounded-full bg-emerald-500" />
                                    <span className="min-w-0">
                                      <span className="block truncate text-xs font-medium text-zinc-700">
                                        {project.name || `项目 ${projectIndex + 1}`}
                                      </span>
                                      {project.metrics.length > 0 && (
                                        <span className="block truncate text-[11px] text-zinc-500">
                                          {project.metrics.slice(0, 2).join('；')}
                                        </span>
                                      )}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      )}

      {profile && (
        <details className="mt-3 rounded-lg border border-zinc-200 bg-zinc-50/70 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-zinc-600">
            <span className="inline-flex items-center gap-1.5">
              <Sparkles className="size-3.5 text-emerald-600" aria-hidden="true" />
              简历 AI 分析
            </span>
            <span className="ml-2 text-[11px] font-normal text-zinc-400">
              {profile.highlights.length} 个亮点 · {profile.risk_points.length} 个风险点
            </span>
          </summary>
          <div className="mt-2 space-y-3 border-t border-zinc-200 pt-2 text-xs leading-5 text-zinc-600">
            {profile.highlights.length > 0 && (
              <div>
                <p className="font-semibold text-zinc-700">核心亮点</p>
                <ul className="mt-1 space-y-1">
                  {profile.highlights.slice(0, 3).map((highlight, index) => (
                    <li key={`${highlight}-${index}`} className="flex gap-1.5">
                      <span className="mt-2 size-1 shrink-0 rounded-full bg-emerald-500" />
                      <span>{highlight}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {profile.risk_points.length > 0 && (
              <div>
                <p className="font-semibold text-amber-800">风险与待深挖</p>
                <ul className="mt-1 space-y-1 text-amber-800/90">
                  {profile.risk_points.slice(0, 4).map((risk, index) => (
                    <li key={`${risk}-${index}`} className="flex gap-1.5">
                      <span className="mt-2 size-1 shrink-0 rounded-full bg-amber-500" />
                      <span>{risk}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>
      )}

      {profile && (
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-zinc-100 pt-3">
          <p className="text-xs text-zinc-500">
            {profile.experiences.length} 段工作经历 · {profile.projects.length} 个项目条目
          </p>
          <button
            type="button"
            className="inline-flex items-center gap-1 text-xs font-medium text-zinc-600 transition hover:text-zinc-900"
            onClick={() => setShowImport((current) => !current)}
            aria-expanded={showImport}
          >
            <FileUp className="size-3.5" aria-hidden="true" />
            {showImport ? '收起导入' : '更换简历'}
          </button>
        </div>
      )}

      {(!profile || showImport) && (
        <div className={profile ? 'mt-3 border-t border-zinc-100 pt-3' : 'mt-4'}>
          <div className="inline-flex rounded-lg border border-zinc-300 bg-white p-1">
            <button
              type="button"
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                mode === 'file' ? 'bg-emerald-600 text-white' : 'text-zinc-600 hover:bg-zinc-50'
              }`}
              onClick={() => setMode('file')}
            >
              <FileUp className="size-3.5" aria-hidden="true" />
              上传简历
            </button>
            <button
              type="button"
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                mode === 'paste' ? 'bg-emerald-600 text-white' : 'text-zinc-600 hover:bg-zinc-50'
              }`}
              onClick={() => setMode('paste')}
            >
              <ClipboardPaste className="size-3.5" aria-hidden="true" />
              粘贴文本
            </button>
          </div>

          {mode === 'file' && (
            <div className="mt-3 space-y-3">
              <label
                htmlFor="resume-file"
                className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 px-3 py-3 text-xs text-zinc-600 transition hover:border-emerald-400 hover:bg-emerald-50/40"
              >
                <FileUp className="size-4 shrink-0 text-zinc-400" aria-hidden="true" />
                <span className="min-w-0 truncate">
                  {file ? file.name : '选择简历文件（PDF / DOCX / TXT / Markdown）'}
                </span>
              </label>
              <input
                id="resume-file"
                type="file"
                className="hidden"
                accept={RESUME_EXTENSIONS.join(',')}
                onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
              />
              {file && (
                <input
                  className="text-input"
                  value={fileTitle}
                  onChange={(event) => setFileTitle(event.target.value)}
                  placeholder="档案名称，例如：测试工程师-2026"
                  aria-label="简历档案名称"
                />
              )}
              <button
                type="button"
                className="primary-btn w-full"
                onClick={handleFileImport}
                disabled={!file || Boolean(busy)}
              >
                {busy === 'file' ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="size-4" aria-hidden="true" />
                )}
                {busy === 'file' ? 'AI 正在抽取关键信息' : '解析并设为当前简历'}
              </button>
              {busy === 'file' && (
                <p className="text-xs leading-5 text-zinc-500">
                  内容较长的简历会自动分段解析，请稍候。中途失败时重新上传同一份简历会接着上次的进度继续。
                </p>
              )}
            </div>
          )}

          {mode === 'paste' && (
            <div className="mt-3 space-y-3">
              <input
                className="text-input"
                value={pasteTitle}
                onChange={(event) => setPasteTitle(event.target.value)}
                placeholder="档案名称，例如：测试工程师-2026"
                aria-label="简历档案名称"
              />
              <textarea
                className="text-input min-h-36"
                value={pasteContent}
                onChange={(event) => setPasteContent(event.target.value)}
                placeholder="粘贴简历正文，包含项目经历、技术栈和量化结果。"
                aria-label="简历正文"
              />
              <button
                type="button"
                className="primary-btn w-full"
                onClick={handlePasteImport}
                disabled={!pasteContent.trim() || Boolean(busy)}
              >
                {busy === 'paste' ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="size-4" aria-hidden="true" />
                )}
                {busy === 'paste' ? 'AI 正在抽取关键信息' : '解析并设为当前简历'}
              </button>
              {busy === 'paste' && (
                <p className="text-xs leading-5 text-zinc-500">
                  内容较长的简历会自动分段解析，请稍候。中途失败时用同样的内容重试会接着上次的进度继续。
                </p>
              )}
            </div>
          )}
        </div>
      )}

      <details className="mt-3 rounded-lg border border-zinc-200 bg-zinc-50/70 px-3 py-2">
        <summary className="cursor-pointer text-xs font-medium text-zinc-600">
          补充说明（可选）
        </summary>
        <textarea
          id="resume"
          className="text-input mt-2 min-h-24"
          value={resume}
          onChange={(event) => onResumeChange(event.target.value)}
          placeholder="简历之外的补充信息，例如近期学习方向、想重点被追问的项目。"
        />
        {!activeResume && (
          <p className="mt-2 text-xs leading-5 text-amber-700">
            还没有导入简历档案，面试官只能依据这里的文字提问。
          </p>
        )}
      </details>

      {error && <p className="mt-3 text-sm leading-5 text-red-600">{error}</p>}

      {otherResumes.length > 0 && (
        <div className="mt-4 border-t border-zinc-100 pt-3">
          <button
            type="button"
            className="flex items-center gap-1.5 text-xs font-medium text-zinc-600 transition hover:text-zinc-900"
            onClick={() => setShowLibrary(!showLibrary)}
            aria-expanded={showLibrary}
          >
            {showLibrary ? (
              <ChevronDown className="size-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden="true" />
            )}
            其他简历（{otherResumes.length}）
          </button>

          {showLibrary && (
            <ul className="mt-2 space-y-2">
              {otherResumes.map((item) => (
                <li
                  key={item.resume_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-zinc-800">{item.title}</p>
                    <p className="truncate text-xs text-zinc-500">
                      {[item.name, item.headline].filter(Boolean).join(' · ') || '未识别姓名'} ·{' '}
                      {item.project_count} 个项目
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      className="secondary-btn px-2.5 py-1.5 text-xs"
                      onClick={() => handleActivate(item.resume_id)}
                      disabled={Boolean(busy)}
                    >
                      {busy === `activate:${item.resume_id}` ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      ) : null}
                      启用
                    </button>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-zinc-400 transition hover:bg-red-50 hover:text-red-600"
                      onClick={() => handleDelete(item.resume_id)}
                      disabled={Boolean(busy)}
                      aria-label={`删除 ${item.title}`}
                      title={`删除 ${item.title}`}
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
