import { useCallback, useEffect, useRef, useState } from 'react'

const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']

export interface UseAnswerRecorderResult {
  isRecording: boolean
  elapsedSeconds: number
  level: number
  error: string
  startRecording: () => Promise<void>
  stopRecording: () => Promise<Blob | null>
  cancelRecording: () => void
}

/**
 * 模拟面试回答的本地录音器。
 *
 * Web Speech API 依赖浏览器厂商的在线识别服务，服务不可用时不会返回任何文字。
 * 这里始终保留一份录音，实时识别为空时可以把录音送到后端本地 Whisper 兜底。
 */
export function useAnswerRecorder(): UseAnswerRecorderResult {
  const [isRecording, setIsRecording] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState('')
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const stopResolveRef = useRef<((blob: Blob | null) => void) | null>(null)
  const startedAtRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const levelTimerRef = useRef<number | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const tokenRef = useRef(0)

  const cleanup = useCallback(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (levelTimerRef.current) {
      window.clearInterval(levelTimerRef.current)
      levelTimerRef.current = null
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    audioContextRef.current?.close().catch(() => {})
    audioContextRef.current = null
    setIsRecording(false)
    setLevel(0)
  }, [])

  const startRecording = useCallback(async () => {
    if (recorderRef.current?.state === 'recording') return
    if (
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === 'undefined'
    ) {
      return
    }

    const token = tokenRef.current + 1
    tokenRef.current = token
    setError('')
    setElapsedSeconds(0)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (token !== tokenRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const mimeType =
        typeof MediaRecorder.isTypeSupported === 'function'
          ? MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type))
          : undefined
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const blob = chunksRef.current.length
          ? new Blob(chunksRef.current, {
              type: recorder.mimeType || mimeType || 'audio/webm',
            })
          : null
        chunksRef.current = []
        const resolve = stopResolveRef.current
        stopResolveRef.current = null
        resolve?.(blob)
        recorderRef.current = null
        cleanup()
      }
      recorder.start(250)
      recorderRef.current = recorder
      startedAtRef.current = Date.now()
      setIsRecording(true)
      timerRef.current = window.setInterval(() => {
        if (!startedAtRef.current) return
        setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000))
      }, 500)

      // 音量条只是辅助确认麦克风是否收到声音；失败不影响录音和转写。
      try {
        const Ctor =
          window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext })
            .webkitAudioContext
        if (Ctor) {
          const context = new Ctor()
          if (context.state === 'suspended') await context.resume()
          const source = context.createMediaStreamSource(stream)
          const analyser = context.createAnalyser()
          analyser.fftSize = 512
          source.connect(analyser)
          audioContextRef.current = context
          const data = new Uint8Array(analyser.fftSize)
          levelTimerRef.current = window.setInterval(() => {
            analyser.getByteTimeDomainData(data)
            let sum = 0
            for (let index = 0; index < data.length; index += 1) {
              const value = (data[index] - 128) / 128
              sum += value * value
            }
            setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4))
          }, 100)
        }
      } catch {
        // 电平条不可用时，录音仍可正常兜底转写。
      }
    } catch (recordError) {
      if (token !== tokenRef.current) return
      cleanup()
      if (recordError instanceof DOMException && recordError.name === 'NotAllowedError') {
        setError('麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotFoundError') {
        setError('没有检测到麦克风设备，可以改用打字回答。')
      } else if (recordError instanceof DOMException && recordError.name === 'NotReadableError') {
        setError('麦克风暂时被其他应用占用，请关闭占用后重试。')
      } else {
        setError(
          `录音启动失败：${recordError instanceof Error ? recordError.message : '未知错误'}`,
        )
      }
    }
  }, [cleanup])

  const stopRecording = useCallback((): Promise<Blob | null> => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') {
      cleanup()
      return Promise.resolve(null)
    }
    return new Promise<Blob | null>((resolve) => {
      stopResolveRef.current = resolve
      try {
        recorder.stop()
      } catch {
        stopResolveRef.current = null
        cleanup()
        resolve(null)
      }
    })
  }, [cleanup])

  const cancelRecording = useCallback(() => {
    tokenRef.current += 1
    const recorder = recorderRef.current
    stopResolveRef.current = null
    recorderRef.current = null
    if (recorder && recorder.state !== 'inactive') {
      try {
        recorder.stop()
      } catch {
        // 已经结束时忽略。
      }
    }
    cleanup()
  }, [cleanup])

  useEffect(() => () => cancelRecording(), [cancelRecording])

  return {
    isRecording,
    elapsedSeconds,
    level,
    error,
    startRecording,
    stopRecording,
    cancelRecording,
  }
}
