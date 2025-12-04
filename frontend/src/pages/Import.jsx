import React, { useState, useEffect, useRef } from 'react'
import LoadingSpinner from '../components/LoadingSpinner'
import { useAccounts } from '../context/AccountContext'
import { useGlobalProgress } from '../context/GlobalProgressContext'
import { uploadCsv, importDevs, importKols, importDevsMulti, importKolsMulti } from '../services/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Progress } from '@/components/ui/progress'
import {
  AlertCircle,
  Upload,
  FileUp,
  Download,
  RotateCcw,
  CheckCircle,
  XCircle,
  Clock,
  Code,
  Megaphone,
  FileText,
  Smartphone,
  Eye
} from 'lucide-react'

function Import({ isConnected }) {
  const [activeTab, setActiveTab] = useState('devs')
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState([])
  const [isDryRun, setIsDryRun] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [accountMode, setAccountMode] = useState('single')
  const [selectedAccount, setSelectedAccount] = useState('')
  const [localSelectedAccounts, setLocalSelectedAccounts] = useState([])
  const [uploadedCsvPath, setUploadedCsvPath] = useState('')

  // Real-time progress state
  const [progressLogs, setProgressLogs] = useState([])
  const [currentProgress, setCurrentProgress] = useState({ processed: 0, total: 0, message: '' })
  const progressContainerRef = useRef(null)

  // Use shared account context
  const {
    accounts,
    selectedAccounts: contextSelectedAccounts,
    getPhoneArray
  } = useAccounts()

  // Global progress context for real-time updates
  const {
    isConnected: wsConnected,
    operationProgress,
    logs,
    operationResult,
    batchDelay,
    startOperation,
    stopOperation,
    clearProgress,
    getCombinedStats,
    formatEta,
    formatSpeed
  } = useGlobalProgress()

  // Auto-scroll progress logs to bottom
  useEffect(() => {
    if (progressContainerRef.current) {
      progressContainerRef.current.scrollTop = progressContainerRef.current.scrollHeight
    }
  }, [progressLogs])

  // Handle WebSocket progress updates
  useEffect(() => {
    if (operationProgress?.accounts) {
      const accountData = Object.values(operationProgress.accounts)[0]
      if (accountData) {
        setCurrentProgress({
          processed: accountData.progress || 0,
          total: accountData.total || 0,
          message: accountData.message || ''
        })
      }
    }
  }, [operationProgress])

  // Handle WebSocket logs
  useEffect(() => {
    if (logs && logs.length > 0) {
      setProgressLogs(logs.map(log => ({
        timestamp: new Date(log.timestamp).toLocaleTimeString(),
        message: log.message,
        level: log.level
      })))
    }
  }, [logs])

  // Handle operation completion
  useEffect(() => {
    if (operationResult) {
      setImporting(false)
      if (operationResult.status === 'completed' && operationResult.results) {
        setImportResult({
          added: operationResult.results.added || 0,
          skipped: operationResult.results.skipped || 0,
          failed: operationResult.results.failed || 0,
          dry_run: operationResult.results.dry_run
        })
      } else if (operationResult.error) {
        alert(`Import failed: ${operationResult.error}`)
      }
      // Note: Don't call stopOperation here - let the GlobalProgressPanel auto-hide
    }
  }, [operationResult])

  // Initialize local selection from context when accounts load
  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      const defaultPhone = contextSelectedAccounts.length > 0
        ? contextSelectedAccounts[0].phone
        : accounts[0].phone
      setSelectedAccount(defaultPhone)
    }
    if (contextSelectedAccounts.length > 0 && localSelectedAccounts.length === 0) {
      setLocalSelectedAccounts(contextSelectedAccounts.map(acc => acc.phone))
    }
  }, [accounts, contextSelectedAccounts])

  const handleFileSelect = async (e) => {
    const selectedFile = e.target.files[0]
    if (selectedFile) {
      setFile(selectedFile)
      const reader = new FileReader()
      reader.onload = (event) => {
        const csv = event.target.result
        const lines = csv.split('\n').slice(0, 11)
        setPreview(lines)
      }
      reader.readAsText(selectedFile)

      try {
        const uploadResponse = await uploadCsv(selectedFile)
        if (uploadResponse.success) {
          setUploadedCsvPath(uploadResponse.data.path)
        }
      } catch (err) {
        console.error('Upload failed:', err)
      }
    }
  }

  const handleImport = async () => {
    if (!file || !uploadedCsvPath) {
      alert('Please select and upload a file first')
      return
    }

    if (accountMode === 'single' && !selectedAccount) {
      alert('Please select an account')
      return
    }

    if (accountMode === 'multiple' && localSelectedAccounts.length === 0) {
      alert('Please select at least one account')
      return
    }

    setImporting(true)
    setImportResult(null)
    setProgressLogs([])
    setCurrentProgress({ processed: 0, total: 0, message: 'Starting import...' })
    clearProgress()

    try {
      let response
      if (accountMode === 'single') {
        if (activeTab === 'devs') {
          response = await importDevs({ csv_path: uploadedCsvPath, dry_run: isDryRun, phone: selectedAccount })
        } else {
          response = await importKols({ csv_path: uploadedCsvPath, dry_run: isDryRun, phone: selectedAccount })
        }
      } else {
        const accountPhones = localSelectedAccounts
        if (activeTab === 'devs') {
          response = await importDevsMulti(uploadedCsvPath, accountPhones, isDryRun)
        } else {
          response = await importKolsMulti(uploadedCsvPath, accountPhones, isDryRun)
        }
      }

      if (response.success) {
        if (accountMode === 'single' && response.data.operation_id) {
          // Start tracking via global progress context
          const opType = activeTab === 'devs' ? 'import_devs' : 'import_kols'
          startOperation(response.data.operation_id, opType)
        } else if (accountMode === 'multiple' && response.data.import_results_csv) {
          const results = response.data.results || {}
          let totalAdded = 0
          let totalSkipped = 0
          let totalFailed = 0

          Object.values(results).forEach(accountResults => {
            if (Array.isArray(accountResults)) {
              accountResults.forEach(r => {
                if (r.status === 'added') totalAdded++
                else if (r.status === 'skipped') totalSkipped++
                else if (r.status === 'failed') totalFailed++
              })
            }
          })

          setImportResult({
            added: totalAdded,
            skipped: totalSkipped,
            failed: totalFailed,
            multiAccount: true,
            results: results,
            csvPath: response.data.import_results_csv
          })
          setImporting(false)
        }
      } else {
        alert(`Import failed: ${response.error?.message || 'Unknown error'}`)
        setImporting(false)
      }
    } catch (err) {
      alert(`Import error: ${err.message}`)
      setImporting(false)
    }
  }

  if (!isConnected) {
    return (
      <div className="p-6">
        <Alert variant="destructive" className="border-red-500/30 bg-red-500/5">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            API connection required. Make sure the Flask server is running.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="p-6 animate-fade-in">
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-2 max-w-md mb-6 p-1 h-12">
          <TabsTrigger value="devs" className="flex items-center gap-2 h-10 data-[state=active]:shadow-glow">
            <Code className="h-4 w-4" />
            Import Developers
          </TabsTrigger>
          <TabsTrigger value="kols" className="flex items-center gap-2 h-10 data-[state=active]:shadow-glow">
            <Megaphone className="h-4 w-4" />
            Import KOLs
          </TabsTrigger>
        </TabsList>

        <TabsContent value={activeTab} className="mt-0">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileUp className="h-5 w-5 text-primary" />
                {activeTab === 'devs' ? 'Upload Developer CSV' : 'Upload KOL CSV'}
              </CardTitle>
              <CardDescription>
                Select a CSV file containing {activeTab === 'devs' ? 'developer' : 'KOL'} usernames to import
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* File Upload */}
              <div className="border-2 border-dashed border-border rounded-xl p-8 text-center hover:border-primary hover:bg-primary/5 transition-all cursor-pointer group">
                <input
                  type="file"
                  accept=".csv"
                  onChange={handleFileSelect}
                  className="hidden"
                  id="csv-input"
                />
                <label htmlFor="csv-input" className="cursor-pointer">
                  <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto mb-4 group-hover:bg-primary/20 group-hover:scale-110 transition-all">
                    <FileUp className="h-8 w-8 text-primary" />
                  </div>
                  <p className="text-foreground font-medium">Drag and drop your CSV file here</p>
                  <p className="text-muted-foreground text-sm mt-1">or click to browse</p>
                </label>
              </div>

              {/* File Info */}
              {file && (
                <div className="flex items-center gap-4 p-4 rounded-xl bg-green-500/10 border border-green-500/30">
                  <div className="w-10 h-10 rounded-lg bg-green-500/20 flex items-center justify-center">
                    <CheckCircle className="h-5 w-5 text-green-500" />
                  </div>
                  <div className="flex-1">
                    <p className="font-medium text-foreground">{file.name}</p>
                    <p className="text-sm text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
                  </div>
                  <FileText className="h-5 w-5 text-green-500" />
                </div>
              )}

              {/* Account Selection */}
              {file && accounts.length > 0 && (
                <div className="p-5 rounded-xl bg-accent/30 border border-border/50 space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-primary/20 flex items-center justify-center">
                      <Smartphone className="h-5 w-5 text-primary" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-foreground">Account Selection</h4>
                      <p className="text-sm text-muted-foreground">Choose which account(s) to import to</p>
                    </div>
                  </div>

                  <RadioGroup value={accountMode} onValueChange={setAccountMode} className="flex gap-4">
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="single" id="single" />
                      <Label htmlFor="single" className="cursor-pointer">Single Account</Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="multiple" id="multiple" />
                      <Label htmlFor="multiple" className="cursor-pointer">Multiple Accounts (Equal Distribution)</Label>
                    </div>
                  </RadioGroup>

                  {accountMode === 'single' ? (
                    <Select value={selectedAccount} onValueChange={setSelectedAccount}>
                      <SelectTrigger className="h-11">
                        <SelectValue placeholder="Select an account" />
                      </SelectTrigger>
                      <SelectContent>
                        {accounts.map(acc => (
                          <SelectItem key={acc.phone} value={acc.phone}>
                            {acc.name || acc.phone} ({acc.phone})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <div className="space-y-3">
                      <p className="text-sm text-muted-foreground">Select accounts (contacts will be distributed equally):</p>
                      <div className="flex flex-wrap gap-2">
                        {accounts.map(acc => (
                          <button
                            key={acc.phone}
                            onClick={() => {
                              if (localSelectedAccounts.includes(acc.phone)) {
                                setLocalSelectedAccounts(localSelectedAccounts.filter(p => p !== acc.phone))
                              } else {
                                setLocalSelectedAccounts([...localSelectedAccounts, acc.phone])
                              }
                            }}
                            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border transition-all duration-200 ${
                              localSelectedAccounts.includes(acc.phone)
                                ? 'border-primary bg-primary text-primary-foreground'
                                : 'border-border bg-background hover:border-primary/50'
                            }`}
                          >
                            {localSelectedAccounts.includes(acc.phone) && <CheckCircle className="h-4 w-4" />}
                            <span className="text-sm font-medium">{acc.name || acc.phone}</span>
                          </button>
                        ))}
                      </div>
                      {localSelectedAccounts.length > 0 && (
                        <p className="text-sm text-primary">
                          {localSelectedAccounts.length} account(s) selected - contacts will be split equally
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Dry Run Checkbox */}
              {file && (
                <div className="flex items-start space-x-3 p-4 rounded-xl bg-accent/30 border border-border/50">
                  <Checkbox
                    id="dry-run"
                    checked={isDryRun}
                    onCheckedChange={setIsDryRun}
                    className="mt-1"
                  />
                  <div className="grid gap-1.5 leading-none">
                    <Label htmlFor="dry-run" className="cursor-pointer font-medium flex items-center gap-2">
                      <Eye className="h-4 w-4 text-muted-foreground" />
                      Preview without importing (Dry Run)
                    </Label>
                    <p className="text-sm text-muted-foreground">
                      Shows what will be added/skipped without making changes
                    </p>
                  </div>
                </div>
              )}

              {/* Preview */}
              {preview.length > 0 && (
                <div className="space-y-3">
                  <Label className="text-base font-semibold flex items-center gap-2">
                    <FileText className="h-4 w-4 text-primary" />
                    File Preview (first 10 rows)
                  </Label>
                  <div className="bg-muted/50 rounded-xl p-4 font-mono text-xs overflow-x-auto max-h-64 overflow-y-auto border border-border/50">
                    {preview.map((line, idx) => (
                      <div key={idx} className={`${idx === 0 ? 'text-primary font-semibold' : 'text-muted-foreground'}`}>
                        {line}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Action Button */}
              {file && !importing && !importResult && (
                <Button onClick={handleImport} className="w-full h-12 text-base hover-scale" size="lg">
                  <Upload className="h-5 w-5 mr-2" />
                  {isDryRun ? 'Preview Import' : 'Start Import'}
                </Button>
              )}

              {/* Real-Time Progress Panel */}
              {importing && (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="font-medium text-foreground">
                        {currentProgress.total > 0
                          ? `Processing ${currentProgress.processed} of ${currentProgress.total} contacts`
                          : 'Initializing...'}
                      </span>
                      <span className="text-primary font-semibold">
                        {currentProgress.total > 0
                          ? `${Math.round((currentProgress.processed / currentProgress.total) * 100)}%`
                          : '0%'}
                      </span>
                    </div>
                    <Progress
                      value={currentProgress.total > 0 ? (currentProgress.processed / currentProgress.total) * 100 : 0}
                      className="h-3"
                    />
                  </div>

                  <div className="flex items-center gap-3 p-4 rounded-xl bg-primary/5 border border-primary/20">
                    <Clock className="h-5 w-5 text-primary animate-pulse" />
                    <p className="font-medium text-foreground">{currentProgress.message || 'Starting import...'}</p>
                  </div>

                  <div className="border rounded-xl overflow-hidden">
                    <div className="bg-muted px-4 py-3 border-b flex items-center gap-2">
                      <span className="relative flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                      </span>
                      <span className="font-medium text-sm">Live Progress</span>
                    </div>
                    <div
                      ref={progressContainerRef}
                      className="p-4 max-h-64 overflow-y-auto font-mono text-xs space-y-1 bg-background"
                    >
                      {progressLogs.length === 0 ? (
                        <p className="text-muted-foreground">Waiting for progress updates...</p>
                      ) : (
                        progressLogs.map((log, idx) => (
                          <div
                            key={idx}
                            className={`flex gap-2 ${
                              log.level === 'success' ? 'text-green-500' :
                              log.level === 'error' ? 'text-red-500' :
                              log.level === 'warning' ? 'text-yellow-500' :
                              'text-muted-foreground'
                            }`}
                          >
                            <span className="text-muted-foreground/70">[{log.timestamp}]</span>
                            <span>{log.message}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  {/* Stats from global progress */}
                  {(() => {
                    const stats = getCombinedStats()
                    return (
                      <div className="grid grid-cols-4 gap-4">
                        <div className="p-4 rounded-xl stat-card-green border">
                          <div className="flex items-center gap-3">
                            <CheckCircle className="h-6 w-6 text-green-500" />
                            <div>
                              <p className="text-xs text-green-400 font-medium">Added</p>
                              <p className="text-2xl font-bold text-foreground">{stats.added}</p>
                            </div>
                          </div>
                        </div>
                        <div className="p-4 rounded-xl stat-card-yellow border">
                          <div className="flex items-center gap-3">
                            <Clock className="h-6 w-6 text-yellow-500" />
                            <div>
                              <p className="text-xs text-yellow-400 font-medium">Skipped</p>
                              <p className="text-2xl font-bold text-foreground">{stats.skipped}</p>
                            </div>
                          </div>
                        </div>
                        <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/30">
                          <div className="flex items-center gap-3">
                            <XCircle className="h-6 w-6 text-red-500" />
                            <div>
                              <p className="text-xs text-red-400 font-medium">Failed</p>
                              <p className="text-2xl font-bold text-foreground">{stats.failed}</p>
                            </div>
                          </div>
                        </div>
                        <div className="p-4 rounded-xl bg-blue-500/10 border border-blue-500/30">
                          <div className="flex items-center gap-3">
                            <div className="h-6 w-6 flex items-center justify-center text-blue-500 font-bold text-sm">%</div>
                            <div>
                              <p className="text-xs text-blue-400 font-medium">Success</p>
                              <p className="text-2xl font-bold text-foreground">{stats.successRate}%</p>
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  })()}

                  {/* Batch delay indicator */}
                  {batchDelay && (
                    <div className="p-4 rounded-xl bg-yellow-500/10 border border-yellow-500/30">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium text-yellow-500 flex items-center gap-2">
                          <Clock className="h-4 w-4" />
                          Batch {batchDelay.batch_number} of ~{batchDelay.total_batches_estimate}
                        </span>
                        <span className="text-sm text-muted-foreground">
                          {batchDelay.reason === 'slowdown' ? 'Slowing down...' : 'Waiting between batches...'}
                        </span>
                      </div>
                      <Progress value={batchDelay.progress} className="h-2 [&>div]:bg-yellow-500" />
                    </div>
                  )}

                  {/* Speed and ETA */}
                  {(() => {
                    const stats = getCombinedStats()
                    return (
                      <div className="flex items-center justify-between text-sm text-muted-foreground pt-2">
                        <span>Speed: {formatSpeed(stats.speed)}</span>
                        <span>ETA: {formatEta(stats.etaSeconds)}</span>
                      </div>
                    )
                  })()}
                </div>
              )}

              {/* Results */}
              {importResult && (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4">
                    <Card className="stat-card-green border">
                      <CardContent className="p-5">
                        <div className="flex items-center gap-3">
                          <div className="w-12 h-12 rounded-xl bg-green-500/20 flex items-center justify-center">
                            <CheckCircle className="h-6 w-6 text-green-500" />
                          </div>
                          <div>
                            <p className="text-sm font-medium text-green-400">Added</p>
                            <p className="text-3xl font-bold text-foreground">{importResult.added}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                    <Card className="stat-card-yellow border">
                      <CardContent className="p-5">
                        <div className="flex items-center gap-3">
                          <div className="w-12 h-12 rounded-xl bg-yellow-500/20 flex items-center justify-center">
                            <Clock className="h-6 w-6 text-yellow-500" />
                          </div>
                          <div>
                            <p className="text-sm font-medium text-yellow-400">Skipped</p>
                            <p className="text-3xl font-bold text-foreground">{importResult.skipped}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                    <Card className="bg-red-500/10 border border-red-500/30">
                      <CardContent className="p-5">
                        <div className="flex items-center gap-3">
                          <div className="w-12 h-12 rounded-xl bg-red-500/20 flex items-center justify-center">
                            <XCircle className="h-6 w-6 text-red-500" />
                          </div>
                          <div>
                            <p className="text-sm font-medium text-red-400">Failed</p>
                            <p className="text-3xl font-bold text-foreground">{importResult.failed}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </div>

                  {importResult.multiAccount && importResult.csvPath && (
                    <div className="flex items-start gap-3 p-4 rounded-xl bg-primary/5 border border-primary/20">
                      <Download className="h-5 w-5 text-primary mt-0.5" />
                      <div className="flex-1">
                        <p className="font-semibold text-foreground">Import Results CSV Generated</p>
                        <p className="text-sm text-muted-foreground mt-1">
                          Detailed results showing which usernames were added to which accounts
                        </p>
                        <Button variant="outline" size="sm" className="mt-3 hover-scale" asChild>
                          <a href={`http://localhost:5000/${importResult.csvPath.replace(/\\/g, '/')}`} download>
                            <Download className="h-4 w-4 mr-2" />
                            Download Results CSV
                          </a>
                        </Button>
                      </div>
                    </div>
                  )}

                  <Button
                    onClick={() => {
                      setFile(null)
                      setPreview([])
                      setImportResult(null)
                      setUploadedCsvPath('')
                      setLocalSelectedAccounts([])
                      setProgressLogs([])
                      setCurrentProgress({ processed: 0, total: 0, message: '' })
                      clearProgress()
                    }}
                    variant="outline"
                    className="w-full h-12 text-base hover-scale"
                    size="lg"
                  >
                    <RotateCcw className="h-5 w-5 mr-2" />
                    Import Another File
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

export default Import
