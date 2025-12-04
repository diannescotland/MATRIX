import React, { useState, useEffect, useRef } from 'react'
import LoadingSpinner from '../components/LoadingSpinner'
import MultiAccountSelector from '../components/MultiAccountSelector'
import { useAccounts } from '../context/AccountContext'
import { useWebSocket } from '../hooks/useWebSocket'
import { startMultiAccountOperation, invalidateCache } from '../services/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Alert, AlertDescription } from '@/components/ui/alert'
// Select import removed - using MultiAccountSelector instead
import { AlertCircle, Play, Square, RotateCcw, Download, User, Users, Clock } from 'lucide-react'

function Operations({ isConnected }) {
  const [selectedOperation, setSelectedOperation] = useState('scan')
  const [isRunning, setIsRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [progressMessage, setProgressMessage] = useState('')
  const [logs, setLogs] = useState([])
  const [result, setResult] = useState(null)
  const [timeframe, setTimeframe] = useState('48')
  const [exportCsv, setExportCsv] = useState(true)
  const [csvFiles, setCsvFiles] = useState({})
  const [operationSelectedAccounts, setOperationSelectedAccounts] = useState([])
  const [repliedContacts, setRepliedContacts] = useState([])
  const [rateLimit, setRateLimit] = useState(null)  // Rate limit info: {wait_seconds, message, expires_at}
  const progressIntervalRef = useRef(null)
  const rateLimitTimerRef = useRef(null)

  // Use shared account context
  const {
    accounts,
    selectedAccounts,
    hasSelection
  } = useAccounts()

  // WebSocket for real-time progress
  const {
    subscribe,
    unsubscribe,
    operationProgress,
    operationResult,
    clearProgress,
    getOverallProgress
  } = useWebSocket()

  // Sync operation accounts with context selection when it changes
  useEffect(() => {
    if (selectedAccounts.length > 0 && operationSelectedAccounts.length === 0) {
      setOperationSelectedAccounts(selectedAccounts)
    }
  }, [selectedAccounts])

  // Cleanup progress polling and rate limit timer on unmount
  useEffect(() => {
    return () => {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current)
      }
      if (rateLimitTimerRef.current) {
        clearInterval(rateLimitTimerRef.current)
      }
    }
  }, [])

  // Rate limit countdown timer
  useEffect(() => {
    if (rateLimit && rateLimit.remaining_seconds > 0) {
      rateLimitTimerRef.current = setInterval(() => {
        setRateLimit(prev => {
          if (!prev) return null
          const newRemaining = prev.remaining_seconds - 1
          if (newRemaining <= 0) {
            clearInterval(rateLimitTimerRef.current)
            return null
          }
          return { ...prev, remaining_seconds: newRemaining }
        })
      }, 1000)
      return () => {
        if (rateLimitTimerRef.current) {
          clearInterval(rateLimitTimerRef.current)
        }
      }
    }
  }, [rateLimit?.wait_seconds])

  // Handle WebSocket progress updates
  useEffect(() => {
    if (operationProgress?.accounts) {
      // Update overall progress
      setProgress(getOverallProgress())

      // Aggregate logs from all accounts
      const allLogs = []
      Object.entries(operationProgress.accounts).forEach(([phone, data]) => {
        const account = accounts.find(a => a.phone === phone)
        const accountName = account?.name || phone.slice(-4)
        if (data.message) {
          allLogs.push(`[${accountName}] ${data.message}`)
        }
      })
      if (allLogs.length > 0) {
        setLogs(prev => {
          const existingSet = new Set(prev)
          const newLogs = allLogs.filter(log => !existingSet.has(log))
          if (newLogs.length > 0) {
            return [...prev, ...newLogs]
          }
          return prev
        })
      }
    }
  }, [operationProgress, accounts, getOverallProgress])

  // Handle WebSocket operation completion
  useEffect(() => {
    if (operationResult) {
      setIsRunning(false)
      setProgress(100)
      stopProgressPolling()

      if (operationResult.status === 'completed') {
        // Process results based on operation type
        const results = operationResult.results || {}

        // Aggregate results from all accounts
        let totalReplies = 0
        let totalUpdated = 0
        let totalDevReplied = 0
        let totalKolReplied = 0
        const repliedList = []

        Object.entries(results).forEach(([phone, data]) => {
          if (data && !data.error) {
            totalReplies += data.replies_found || 0
            totalUpdated += data.updated_count || 0
            totalDevReplied += data.dev_replied || 0
            totalKolReplied += data.kol_replied || 0

            // Collect replied contacts from each account
            if (data.scan_results) {
              Object.keys(data.scan_results).forEach(name => {
                repliedList.push(parseContactName(name))
              })
            }
          }
        })

        setRepliedContacts(repliedList)

        // Invalidate cache for fresh dashboard data
        invalidateCache('/stats')
        invalidateCache('/accounts')

        setResult({
          success: true,
          message: `Operation completed on ${Object.keys(results).length} account(s)`,
          multiAccount: Object.keys(results).length > 1,
          stats: {
            replies_detected: totalReplies,
            updated: totalUpdated,
            dev_replied: totalDevReplied,
            kol_replied: totalKolReplied,
            accounts_processed: Object.keys(results).length
          },
          perAccountResults: results
        })
      } else if (operationResult.error) {
        setResult({
          success: false,
          message: operationResult.error
        })
      }

      unsubscribe()
    }
  }, [operationResult, unsubscribe])

  // Start polling for progress updates (legacy fallback)
  const startProgressPolling = () => {
    // Clear any existing interval
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current)
    }

    // Poll every 1 second for real-time feel
    progressIntervalRef.current = setInterval(async () => {
      try {
        const statusResponse = await getStatus()
        if (statusResponse.success && statusResponse.data) {
          const { progress: currentProgress, total, status, message, logs: serverLogs } = statusResponse.data

          // Calculate percentage
          const percentage = total > 0 ? Math.round((currentProgress / total) * 100) : 0
          setProgress(percentage)

          // Update message
          if (message && message !== progressMessage) {
            setProgressMessage(message)
          }

          // Merge server logs (real-time updates from scan_for_replies)
          if (serverLogs && serverLogs.length > 0) {
            setLogs(prev => {
              const existingMessages = new Set(prev)
              const newLogs = serverLogs
                .map(log => log.message || log)
                .filter(msg => !existingMessages.has(msg))
              if (newLogs.length > 0) {
                return [...prev, ...newLogs]
              }
              return prev
            })
          }

          // If operation is idle or completed, stop polling
          if (status === 'idle') {
            clearInterval(progressIntervalRef.current)
            progressIntervalRef.current = null
          }
        }
      } catch (err) {
        console.error('Error polling status:', err)
      }
    }, 1000)
  }

  // Stop polling for progress updates
  const stopProgressPolling = () => {
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current)
      progressIntervalRef.current = null
    }
  }

  const operations = [
    {
      id: 'scan',
      label: 'Scan & Update Status',
      icon: '🔍',
      description: 'Detect replies from blue contacts and auto-update their status to yellow',
      estimatedTime: '5-7 minutes per 100 dialogs',
    },
    {
      id: 'folders',
      label: 'Organize into Folders',
      icon: '📁',
      description: 'Create 4 emoji-based folders and organize your contacts',
      estimatedTime: '2-5 minutes',
    },
    {
      id: 'backup',
      label: 'Backup All Contacts',
      icon: '💾',
      description: 'Export all Telegram contacts to CSV file for backup',
      estimatedTime: '30 seconds - 2 minutes',
    },
  ]

  // Parse contact name to extract type and clean name
  const parseContactName = (name) => {
    const isKol = name.includes('📢')
    const isDev = name.includes('💻')
    // Remove emoji prefixes and clean up the name
    let cleanName = name
      .replace(/🔵|🟡/g, '')
      .replace(/📢|💻/g, '')
      .trim()

    // Extract username if present (after |)
    const parts = cleanName.split('|')
    const displayName = parts[0].trim()
    const username = parts.length > 1 ? parts[1].trim() : null

    return {
      type: isKol ? 'kol' : isDev ? 'dev' : 'unknown',
      displayName,
      username,
      originalName: name
    }
  }

  const handleStartOperation = async () => {
    if (isRunning) return

    if (operationSelectedAccounts.length === 0) {
      alert('Please select at least one account to run the operation on')
      return
    }

    const phones = operationSelectedAccounts.map(acc => acc.phone)
    const accountNames = operationSelectedAccounts.map(acc => acc.name || acc.phone.slice(-4)).join(', ')

    setIsRunning(true)
    setProgress(0)
    setProgressMessage('')
    setLogs([`Starting ${selectedOperation} on ${phones.length} account(s): ${accountNames}...`])
    setResult(null)
    setCsvFiles({})
    setRepliedContacts([])
    clearProgress()

    try {
      // Build operation params
      const params = selectedOperation === 'scan'
        ? { dialog_limit: 100, hours: parseInt(timeframe), export_csv: exportCsv }
        : {}

      // Start the multi-account operation
      const response = await startMultiAccountOperation(
        selectedOperation,  // 'scan', 'backup', or 'folders'
        phones,
        params
      )

      if (response.success && response.data.operation_id) {
        // Subscribe to WebSocket for real-time progress
        console.log('Subscribing to operation:', response.data.operation_id)
        subscribe(response.data.operation_id)
        // Results will come through operationResult via WebSocket
      } else {
        throw new Error(response.error?.message || 'Failed to start operation')
      }
    } catch (err) {
      // Check if this is a rate limit error
      if (err.errorType === 'rate_limit' || err.status === 429) {
        const rateLimitInfo = err.rateLimit || {}
        setRateLimit({
          wait_seconds: rateLimitInfo.wait_seconds || 300,
          remaining_seconds: rateLimitInfo.wait_seconds || 300,
          message: rateLimitInfo.message || err.message || 'Rate limited by Telegram',
        })
        setResult({
          success: false,
          message: err.message || 'Rate limited by Telegram. Please wait before trying again.',
          isRateLimit: true,
        })
      } else {
        setResult({
          success: false,
          message: `Error: ${err.message}`,
        })
      }
      setIsRunning(false)
    }
  }

  const currentOp = operations.find((op) => op.id === selectedOperation)

  if (!isConnected) {
    return (
      <div className="p-5">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            API connection required. Make sure the Flask server is running.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="p-5">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Operation Selector */}
        <Card>
          <CardHeader>
            <CardTitle>⚙️ Select Operation</CardTitle>
          </CardHeader>
          <CardContent>
            <RadioGroup value={selectedOperation} onValueChange={setSelectedOperation} disabled={isRunning}>
              <div className="space-y-3">
                {operations.map((op) => (
                  <div
                    key={op.id}
                    className={`flex items-start space-x-3 p-4 rounded-lg border-2 transition ${
                      selectedOperation === op.id
                        ? 'border-primary bg-primary/5'
                        : 'border-border hover:border-primary/50'
                    } ${isRunning ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                    onClick={() => !isRunning && setSelectedOperation(op.id)}
                  >
                    <RadioGroupItem value={op.id} id={op.id} className="mt-1" />
                    <Label htmlFor={op.id} className="cursor-pointer flex-1">
                      <p className="font-semibold">
                        {op.icon} {op.label}
                      </p>
                    </Label>
                  </div>
                ))}
              </div>
            </RadioGroup>
          </CardContent>
        </Card>

        {/* Operation Details */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>📋 Operation Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {currentOp && (
                <div className="space-y-4">
                  <div>
                    <Label className="text-sm font-semibold uppercase text-muted-foreground">Description</Label>
                    <p className="mt-1">{currentOp.description}</p>
                  </div>
                  <div>
                    <Label className="text-sm font-semibold uppercase text-muted-foreground">Estimated Time</Label>
                    <p className="mt-1">⏱️ {currentOp.estimatedTime}</p>
                  </div>

                  {/* Account Selector */}
                  <div>
                    <Label className="text-sm font-semibold uppercase text-muted-foreground mb-2 block">
                      Run On Account(s)
                    </Label>
                    <p className="text-xs text-muted-foreground mb-2">
                      Select one or more accounts. Operations run sequentially on each.
                    </p>
                    {accounts.length === 0 ? (
                      <Alert>
                        <AlertCircle className="h-4 w-4" />
                        <AlertDescription>
                          No accounts found. Please add an account first.
                        </AlertDescription>
                      </Alert>
                    ) : (
                      <MultiAccountSelector
                        accounts={accounts}
                        selectedAccounts={operationSelectedAccounts}
                        onSelectionChange={setOperationSelectedAccounts}
                      />
                    )}
                  </div>

                  {/* Timeframe selector for scan operation */}
                  {selectedOperation === 'scan' && (
                    <div>
                      <Label className="text-sm font-semibold uppercase text-muted-foreground mb-2 block">Time Window</Label>
                      <RadioGroup value={timeframe} onValueChange={setTimeframe}>
                        <div className="flex gap-4">
                          <div className="flex items-center space-x-2">
                            <RadioGroupItem value="24" id="24h" />
                            <Label htmlFor="24h" className="cursor-pointer">24 hours</Label>
                          </div>
                          <div className="flex items-center space-x-2">
                            <RadioGroupItem value="48" id="48h" />
                            <Label htmlFor="48h" className="cursor-pointer">48 hours</Label>
                          </div>
                          <div className="flex items-center space-x-2">
                            <RadioGroupItem value="168" id="7d" />
                            <Label htmlFor="7d" className="cursor-pointer">7 days</Label>
                          </div>
                        </div>
                      </RadioGroup>
                    </div>
                  )}

                  {/* CSV Export option for scan operation */}
                  {selectedOperation === 'scan' && (
                    <div className="flex items-start space-x-3">
                      <Checkbox
                        id="export-csv"
                        checked={exportCsv}
                        onCheckedChange={setExportCsv}
                      />
                      <div className="grid gap-1.5 leading-none">
                        <Label htmlFor="export-csv" className="cursor-pointer font-medium">
                          Export to CSV files
                        </Label>
                        <p className="text-sm text-muted-foreground">
                          Generates noreplyDEV_[timeframe].csv and noreplyKOL_[timeframe].csv files
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Progress Bar with Per-Account Status */}
              {isRunning && (
                <div className="space-y-4">
                  {/* Overall Progress */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="font-semibold">
                        Running on {operationSelectedAccounts.length} account(s)...
                      </Label>
                      <span className="text-sm text-muted-foreground">{progress}%</span>
                    </div>
                    <Progress value={progress} className="h-2" />
                  </div>

                  {/* Per-Account Progress */}
                  {operationProgress?.accounts && Object.keys(operationProgress.accounts).length > 0 && (
                    <div className="space-y-2">
                      <Label className="text-sm font-medium text-muted-foreground">Per-Account Status</Label>
                      <div className="grid gap-2">
                        {Object.entries(operationProgress.accounts).map(([phone, data]) => {
                          const account = accounts.find(a => a.phone === phone)
                          const accountName = account?.name || phone.slice(-4)
                          const accountProgress = data.total > 0 ? Math.round((data.progress / data.total) * 100) : 0

                          return (
                            <div key={phone} className="flex items-center justify-between p-2 bg-muted/30 rounded-lg">
                              <div className="flex items-center gap-2 min-w-0 flex-1">
                                <span className="text-lg">
                                  {data.status === 'completed' ? '✅' :
                                   data.status === 'error' ? '❌' :
                                   data.status === 'running' ? '🔄' : '⏳'}
                                </span>
                                <span className="text-sm font-medium truncate">{accountName}</span>
                              </div>
                              <div className="flex items-center gap-3">
                                <Progress value={accountProgress} className="w-20 h-2" />
                                <span className="text-xs text-muted-foreground w-10 text-right">{accountProgress}%</span>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Logs - Real-time updates */}
              {logs.length > 0 && (
                <div>
                  <Label className="font-semibold mb-2 block">📝 Real-time Scan Log</Label>
                  <div className="bg-muted/50 rounded-lg p-4 font-mono text-sm max-h-72 overflow-y-auto space-y-1">
                    {logs.map((log, idx) => (
                      <div
                        key={idx}
                        className={
                          log.includes('✅ REPLY DETECTED')
                            ? 'text-green-600 dark:text-green-400 font-semibold'
                            : log.includes('📧 Dialog')
                            ? 'text-blue-600 dark:text-blue-400'
                            : log.includes('📊')
                            ? 'text-purple-600 dark:text-purple-400'
                            : log.includes('❌')
                            ? 'text-red-500 dark:text-red-400'
                            : 'text-muted-foreground'
                        }
                      >
                        {log}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Rate Limit Alert */}
              {rateLimit && (
                <Alert variant="destructive" className="bg-orange-50 border-orange-300 dark:bg-orange-950 dark:border-orange-800">
                  <Clock className="h-4 w-4 text-orange-600" />
                  <AlertDescription className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-orange-700 dark:text-orange-300">
                        Rate Limited by Telegram
                      </span>
                      <Badge variant="outline" className="text-orange-600 border-orange-400">
                        {Math.floor(rateLimit.remaining_seconds / 60)}m {rateLimit.remaining_seconds % 60}s remaining
                      </Badge>
                    </div>
                    <p className="text-sm text-orange-600 dark:text-orange-400">{rateLimit.message}</p>
                    <Progress
                      value={100 - (rateLimit.remaining_seconds / rateLimit.wait_seconds * 100)}
                      className="h-2 bg-orange-200 dark:bg-orange-900"
                    />
                  </AlertDescription>
                </Alert>
              )}

              {/* Results */}
              {result && (
                <div className="space-y-4">
                  <Alert variant={result.success ? 'default' : 'destructive'}>
                    <AlertDescription className="flex items-center justify-between">
                      <span>{result.success ? '✅' : '❌'} {result.message}</span>
                      {result.dashboardUpdated && (
                        <Badge variant="outline" className="ml-2 text-green-600 border-green-300 bg-green-50">
                          Dashboard Synced
                        </Badge>
                      )}
                    </AlertDescription>
                  </Alert>

                  {result.stats && (
                    <>
                      {/* Main Stats Grid */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {result.stats.replies_detected !== undefined && (
                          <Card className="bg-green-50 dark:bg-green-950 border-green-200 dark:border-green-800">
                            <CardContent className="pt-4 pb-3">
                              <p className="text-xs font-semibold text-green-700 dark:text-green-300 uppercase">Replies Found</p>
                              <p className="text-3xl font-bold text-green-600 dark:text-green-400">{result.stats.replies_detected}</p>
                            </CardContent>
                          </Card>
                        )}
                        {result.stats.updated !== undefined && (
                          <Card className="bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800">
                            <CardContent className="pt-4 pb-3">
                              <p className="text-xs font-semibold text-blue-700 dark:text-blue-300 uppercase">Status Updated</p>
                              <p className="text-3xl font-bold text-blue-600 dark:text-blue-400">{result.stats.updated}</p>
                            </CardContent>
                          </Card>
                        )}
                        {result.stats.processed !== undefined && (
                          <Card>
                            <CardContent className="pt-4 pb-3">
                              <p className="text-xs font-semibold text-muted-foreground uppercase">Seen No Reply</p>
                              <p className="text-3xl font-bold">{result.stats.processed}</p>
                            </CardContent>
                          </Card>
                        )}
                        {result.stats.folders_created !== undefined && (
                          <Card>
                            <CardContent className="pt-4 pb-3">
                              <p className="text-xs font-semibold text-muted-foreground uppercase">Folders Created</p>
                              <p className="text-3xl font-bold">{result.stats.folders_created}</p>
                            </CardContent>
                          </Card>
                        )}
                      </div>

                      {/* Reply Summary by Type */}
                      {(result.stats.kol_replied !== undefined || result.stats.dev_replied !== undefined) && result.stats.replies_detected > 0 && (
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-base flex items-center gap-2">
                              <Users className="h-4 w-4" />
                              Reply Summary
                            </CardTitle>
                          </CardHeader>
                          <CardContent>
                            <div className="grid grid-cols-2 gap-4">
                              <div className="flex items-center gap-3 p-3 bg-purple-50 dark:bg-purple-950 rounded-lg">
                                <div className="text-2xl">📢</div>
                                <div>
                                  <p className="text-sm text-muted-foreground">KOLs Replied</p>
                                  <p className="text-xl font-bold text-purple-600 dark:text-purple-400">{result.stats.kol_replied || 0}</p>
                                </div>
                              </div>
                              <div className="flex items-center gap-3 p-3 bg-orange-50 dark:bg-orange-950 rounded-lg">
                                <div className="text-2xl">💻</div>
                                <div>
                                  <p className="text-sm text-muted-foreground">Devs Replied</p>
                                  <p className="text-xl font-bold text-orange-600 dark:text-orange-400">{result.stats.dev_replied || 0}</p>
                                </div>
                              </div>
                            </div>
                          </CardContent>
                        </Card>
                      )}

                      {/* Who Replied List */}
                      {repliedContacts.length > 0 && (
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-base flex items-center justify-between">
                              <span className="flex items-center gap-2">
                                <User className="h-4 w-4" />
                                Who Replied ({repliedContacts.length})
                              </span>
                              <div className="flex gap-2">
                                <Badge variant="outline" className="text-purple-600">
                                  🟡📢 {repliedContacts.filter(c => c.type === 'kol').length} KOLs
                                </Badge>
                                <Badge variant="outline" className="text-orange-600">
                                  🟡💻 {repliedContacts.filter(c => c.type === 'dev').length} Devs
                                </Badge>
                              </div>
                            </CardTitle>
                          </CardHeader>
                          <CardContent>
                            <div className="max-h-64 overflow-y-auto space-y-2">
                              {repliedContacts.map((contact, idx) => (
                                <div
                                  key={idx}
                                  className={`flex items-center justify-between p-2 rounded-lg ${
                                    contact.type === 'kol'
                                      ? 'bg-purple-50 dark:bg-purple-950/50'
                                      : contact.type === 'dev'
                                      ? 'bg-orange-50 dark:bg-orange-950/50'
                                      : 'bg-muted/50'
                                  }`}
                                >
                                  <div className="flex items-center gap-2">
                                    <span className="text-lg">
                                      🟡{contact.type === 'kol' ? '📢' : contact.type === 'dev' ? '💻' : ''}
                                    </span>
                                    <div>
                                      <p className="font-medium text-sm">{contact.displayName}</p>
                                      {contact.username && (
                                        <p className="text-xs text-muted-foreground">{contact.username}</p>
                                      )}
                                    </div>
                                  </div>
                                  <Badge
                                    variant="outline"
                                    className={
                                      contact.type === 'kol'
                                        ? 'text-purple-600 border-purple-300'
                                        : contact.type === 'dev'
                                        ? 'text-orange-600 border-orange-300'
                                        : ''
                                    }
                                  >
                                    {contact.type === 'kol' ? 'KOL' : contact.type === 'dev' ? 'DEV' : 'Unknown'}
                                  </Badge>
                                </div>
                              ))}
                            </div>
                          </CardContent>
                        </Card>
                      )}

                      {/* No Reply Stats */}
                      {(result.stats.dev_no_reply !== undefined || result.stats.kol_no_reply !== undefined) && (
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-base">Seen But No Reply</CardTitle>
                            <CardDescription>Contacts who saw your message but haven't replied</CardDescription>
                          </CardHeader>
                          <CardContent>
                            <div className="grid grid-cols-2 gap-4">
                              <div className="text-center p-3 bg-muted/50 rounded-lg">
                                <p className="text-sm text-muted-foreground">💻 Devs</p>
                                <p className="text-2xl font-bold">{result.stats.dev_no_reply || 0}</p>
                              </div>
                              <div className="text-center p-3 bg-muted/50 rounded-lg">
                                <p className="text-sm text-muted-foreground">📢 KOLs</p>
                                <p className="text-2xl font-bold">{result.stats.kol_no_reply || 0}</p>
                              </div>
                            </div>
                          </CardContent>
                        </Card>
                      )}
                    </>
                  )}

                  {/* CSV Download Links */}
                  {result.success && selectedOperation === 'scan' && csvFiles && Object.keys(csvFiles).length > 0 && (
                    <Alert>
                      <AlertDescription>
                        <p className="font-semibold mb-3">📥 Download No-Reply CSV Files</p>
                        <div className="flex gap-2">
                          {csvFiles.dev && (
                            <Button variant="outline" size="sm" asChild>
                              <a href={`http://localhost:5000/${csvFiles.dev.replace(/\\/g, '/')}`} download>
                                <Download className="h-4 w-4 mr-2" />
                                noreplyDEV_{timeframe === '24' ? '24h' : timeframe === '48' ? '48h' : '7d'}.csv
                              </a>
                            </Button>
                          )}
                          {csvFiles.kol && (
                            <Button variant="outline" size="sm" asChild>
                              <a href={`http://localhost:5000/${csvFiles.kol.replace(/\\/g, '/')}`} download>
                                <Download className="h-4 w-4 mr-2" />
                                noreplyKOL_{timeframe === '24' ? '24h' : timeframe === '48' ? '48h' : '7d'}.csv
                              </a>
                            </Button>
                          )}
                        </div>
                      </AlertDescription>
                    </Alert>
                  )}

                  {/* Backup Download Link */}
                  {result.success && selectedOperation === 'backup' && csvFiles && csvFiles.backup && (
                    <Alert>
                      <AlertDescription>
                        <p className="font-semibold mb-3">💾 Download Backup File</p>
                        <p className="text-sm mb-3 text-muted-foreground">
                          All Telegram contacts have been exported to CSV format
                        </p>
                        <Button variant="outline" size="sm" asChild>
                          <a href={`http://localhost:5000/${csvFiles.backup.replace(/\\/g, '/')}`} download>
                            <Download className="h-4 w-4 mr-2" />
                            {result.stats.backup_file}
                          </a>
                        </Button>
                      </AlertDescription>
                    </Alert>
                  )}
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex gap-4">
                <Button
                  onClick={handleStartOperation}
                  disabled={isRunning || (rateLimit && rateLimit.remaining_seconds > 0)}
                  className="flex-1"
                  size="lg"
                >
                  {isRunning ? (
                    <>
                      <LoadingSpinner className="h-4 w-4 mr-2" />
                      Running...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4 mr-2" />
                      Start Operation
                    </>
                  )}
                </Button>
                {result && (
                  <Button
                    onClick={() => {
                      setProgress(0)
                      setProgressMessage('')
                      setLogs([])
                      setResult(null)
                      setRepliedContacts([])
                      setRateLimit(null)
                    }}
                    variant="outline"
                    size="lg"
                  >
                    <RotateCcw className="h-4 w-4 mr-2" />
                    Run Again
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

export default Operations
