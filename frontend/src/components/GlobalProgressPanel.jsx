import React, { useEffect, useState } from 'react'
import { useGlobalProgress } from '../context/GlobalProgressContext'
import { Progress } from '@/components/ui/progress'
import {
  X,
  Minus,
  Maximize2,
  Zap,
  Target,
  Users,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Loader2,
  Clock,
} from 'lucide-react'

function GlobalProgressPanel() {
  const {
    panelVisible,
    panelMinimized,
    operationId,
    operationProgress,
    operationResult,
    batchDelay,
    isOperationActive,
    stopOperation,
    hidePanel,
    toggleMinimize,
    getOverallProgress,
    getCombinedStats,
    getOperationDisplayName,
    formatEta,
    formatSpeed,
  } = useGlobalProgress()

  const [autoHideTimer, setAutoHideTimer] = useState(null)

  // Auto-hide panel 5 seconds after completion
  useEffect(() => {
    if (operationResult && (operationResult.status === 'completed' || operationResult.status === 'error')) {
      const timer = setTimeout(() => {
        hidePanel()
      }, 5000)
      setAutoHideTimer(timer)
      return () => clearTimeout(timer)
    }
  }, [operationResult, hidePanel])

  // Don't render if not visible or no operation
  if (!panelVisible || !operationId) {
    return null
  }

  const overallProgress = getOverallProgress()
  const stats = getCombinedStats()
  const operationName = getOperationDisplayName()
  const isComplete = operationResult?.status === 'completed'
  const isError = operationResult?.status === 'error'

  // Handle close - clear auto-hide timer
  const handleClose = () => {
    if (autoHideTimer) {
      clearTimeout(autoHideTimer)
    }
    stopOperation()
  }

  if (panelMinimized) {
    // Minimized view - small bar
    return (
      <div className="fixed bottom-4 right-4 z-50">
        <button
          onClick={toggleMinimize}
          className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-card border border-border shadow-lg hover:shadow-xl transition-all"
        >
          <Loader2 className="h-4 w-4 text-primary animate-spin" />
          <span className="text-sm font-medium">{operationName}</span>
          <span className="text-sm text-muted-foreground">{overallProgress}%</span>
          <Maximize2 className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>
    )
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80">
      <div className="bg-card border border-border rounded-xl shadow-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-accent/30">
          <div className="flex items-center gap-2">
            {isComplete ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : isError ? (
              <XCircle className="h-4 w-4 text-red-500" />
            ) : (
              <Loader2 className="h-4 w-4 text-primary animate-spin" />
            )}
            <span className="text-sm font-medium">
              {isComplete ? 'Complete' : isError ? 'Error' : operationName}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={toggleMinimize}
              className="p-1.5 rounded-lg hover:bg-accent transition-colors"
              title="Minimize"
            >
              <Minus className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
            <button
              onClick={handleClose}
              className="p-1.5 rounded-lg hover:bg-accent transition-colors"
              title="Close"
            >
              <X className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Overall Progress */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Overall Progress</span>
              <span className="font-medium">{overallProgress}%</span>
            </div>
            <Progress
              value={overallProgress}
              className={`h-2.5 ${isComplete ? '[&>div]:bg-green-500' : isError ? '[&>div]:bg-red-500' : ''}`}
            />
          </div>

          {/* Batch Delay Progress (if active) */}
          {batchDelay && !isComplete && !isError && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-yellow-500 flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  Batch {batchDelay.batch_number} of ~{batchDelay.total_batches_estimate}
                </span>
                <span className="text-muted-foreground">
                  {batchDelay.reason === 'slowdown' ? 'Slowing down...' : 'Waiting...'}
                </span>
              </div>
              <Progress
                value={batchDelay.progress}
                className="h-2 [&>div]:bg-yellow-500"
              />
            </div>
          )}

          {/* Stats Grid */}
          <div className="grid grid-cols-4 gap-2">
            {/* Added */}
            <div className="flex flex-col items-center p-2 rounded-lg bg-green-500/10 border border-green-500/20">
              <span className="text-lg font-bold text-green-500">{stats.added}</span>
              <span className="text-[10px] text-green-500/80 uppercase tracking-wide">Added</span>
            </div>

            {/* Skipped */}
            <div className="flex flex-col items-center p-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
              <span className="text-lg font-bold text-yellow-500">{stats.skipped}</span>
              <span className="text-[10px] text-yellow-500/80 uppercase tracking-wide">Skip</span>
            </div>

            {/* Failed */}
            <div className="flex flex-col items-center p-2 rounded-lg bg-red-500/10 border border-red-500/20">
              <span className="text-lg font-bold text-red-500">{stats.failed}</span>
              <span className="text-[10px] text-red-500/80 uppercase tracking-wide">Failed</span>
            </div>

            {/* Success Rate */}
            <div className="flex flex-col items-center p-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <span className="text-lg font-bold text-blue-500">{stats.successRate}%</span>
              <span className="text-[10px] text-blue-500/80 uppercase tracking-wide">Rate</span>
            </div>
          </div>

          {/* Footer Stats */}
          <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t border-border">
            <div className="flex items-center gap-1">
              <Zap className="h-3 w-3" />
              <span>{formatSpeed(stats.speed)}</span>
            </div>
            <div className="flex items-center gap-1">
              <Target className="h-3 w-3" />
              <span>ETA: {formatEta(stats.etaSeconds)}</span>
            </div>
            {operationProgress?.accounts && (
              <div className="flex items-center gap-1">
                <Users className="h-3 w-3" />
                <span>{Object.keys(operationProgress.accounts).length}</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default GlobalProgressPanel
