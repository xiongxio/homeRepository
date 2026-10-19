import { Briefcase, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'

interface TargetJobCardProps {
  company: string
  targetRole: string
  jd: string
  onCompanyChange: (value: string) => void
  onTargetRoleChange: (value: string) => void
  onJdChange: (value: string) => void
}

export function TargetJobCard({
  company,
  targetRole,
  jd,
  onCompanyChange,
  onTargetRoleChange,
  onJdChange,
}: TargetJobCardProps) {
  const [jdExpanded, setJdExpanded] = useState(false)

  return (
    <section className="tool-card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-zinc-900">
          <Briefcase className="size-5 text-emerald-600" aria-hidden="true" />
          投递目标
        </h2>
        <span className="rounded-lg bg-zinc-100 px-2 py-1 text-xs font-medium text-zinc-500">
          本场
        </span>
      </div>
      <p className="mt-1.5 text-xs leading-5 text-zinc-500">
        公司、岗位和 JD 会一起限定本场面试的提问方向。
      </p>

      <div className="lg:grid lg:grid-cols-2 lg:gap-4 2xl:block">
        <div>
          <label htmlFor="company" className="field-label mt-4">
            目标公司
          </label>
          <input
            id="company"
            className="text-input"
            value={company}
            onChange={(event) => onCompanyChange(event.target.value)}
            placeholder="填写投递的公司名称"
          />
        </div>

        <div>
          <label htmlFor="target-role" className="field-label mt-4">
            目标岗位
          </label>
          <input
            id="target-role"
            className="text-input"
            value={targetRole}
            onChange={(event) => onTargetRoleChange(event.target.value)}
            placeholder="例如：机器人整机测试工程师"
          />
          <p className="mt-1 text-[11px] leading-4 text-zinc-400">
            填实际投递岗位名，会作为 JD 首行参与提问。
          </p>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between gap-2">
        <label htmlFor="jd" className="field-label mb-0">
          岗位 JD
        </label>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 transition hover:text-emerald-800"
          onClick={() => setJdExpanded((current) => !current)}
          aria-expanded={jdExpanded}
        >
          {jdExpanded ? (
            <ChevronUp className="size-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="size-3.5" aria-hidden="true" />
          )}
          {jdExpanded ? '收起' : '展开编辑'}
        </button>
      </div>
      <textarea
        id="jd"
        className={`text-input mt-2 ${jdExpanded ? 'min-h-72' : 'min-h-36'}`}
        value={jd}
        onChange={(event) => onJdChange(event.target.value)}
        placeholder="粘贴岗位职责和任职要求；暂时没有 JD 可以留空。"
      />

    </section>
  )
}
