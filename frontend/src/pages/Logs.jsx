import React, { useState, useEffect } from 'react'
import { getLogs } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertCircle, ChevronRight, ChevronDown, Download, Trash2 } from 'lucide-react'

function Logs({ isConnected }) {
  const [filter, setFilter] = useState('all')
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedLog, setExpandedLog] = useState(null)
  const [selectedLogs, setSelectedLogs] = useState(new Set())

  useEffect(() => {
    if (isConnected) {
      fetchLogs()
    } else {
      setLoading(false)
    }
  }, [isConnected, filter])

  const fetchLogs = async () => {
    setLoading(true)
    const result = await getLogs(filter, 1, 50)
    if (result.success) {
      setLogs(result.data?.logs || [])
    }
    setLoading(false)
  }

  const toggleLogSelection = (logId) => {
    const newSelected = new Set(selectedLogs)
    if (newSelected.has(logId)) {
      newSelected.delete(logId)
    } else {
      newSelected.add(logId)
    }
    setSelectedLogs(newSelected)
  }

  const mockLogs = [
    {
      id: 1,
      timestamp: '2025-01-18 14:32:15',
      operation: 'Import Developers',
      status: 'success',
      message: '45 contacts added, 12 skipped, 2 failed',
      details: 'Imported CSV file with 59 rows. Processing took 8 minutes 32 seconds.',
    },
    {
      id: 2,
      timestamp: '2025-01-18 13:15:42',
      operation: 'Scan & Update Status',
      status: 'success',
      message: '150 dialogs checked, 34 status updates',
      details: 'Scanned inbox for replies. Updated 34 contacts from blue to yellow.',
    },
    {
      id: 3,
      timestamp: '2025-01-18 12:01:20',
      operation: 'Organize Folders',
      status: 'success',
      message: '4 folders created/updated, 184 contacts organized',
      details: 'Created emoji folders and organized all contacts.',
    },
    {
      id: 4,
      timestamp: '2025-01-18 10:45:08',
      operation: 'Import KOLs',
      status: 'success',
      message: '28 contacts added, 5 skipped, 1 failed',
      details: 'Imported CSV file with 34 rows.',
    },
    {
      id: 5,
      timestamp: '2025-01-17 16:22:30',
      operation: 'Scan & Update Status',
      status: 'error',
      message: 'Connection timeout during scan',
      details: 'Operation interrupted due to network error. Please retry.',
    },
  ]

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

  const displayLogs = logs.length > 0 ? logs : mockLogs
  const filteredLogs =
    filter === 'all'
      ? displayLogs
      : displayLogs.filter((log) => log.operation.toLowerCase().includes(filter.toLowerCase()))

  const getStatusVariant = (status) => {
    if (status === 'success') return 'success'
    if (status === 'error') return 'destructive'
    return 'secondary'
  }

  return (
    <div className="p-5">
      {/* Filters */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>🔍 Filter Logs</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            <Button
              variant={filter === 'all' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter('all')}
            >
              All Logs
            </Button>
            <Button
              variant={filter === 'import' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter('import')}
            >
              Imports
            </Button>
            <Button
              variant={filter === 'scan' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter('scan')}
            >
              Scans
            </Button>
            <Button
              variant={filter === 'organize' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter('organize')}
            >
              Folder Organization
            </Button>
            <Button
              variant={filter === 'error' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter('error')}
            >
              Errors
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Logs List */}
      <Card>
        <CardHeader>
          <CardTitle>📋 Operation Logs</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <LoadingSpinner message="Loading logs..." />
          ) : filteredLogs.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-muted-foreground font-medium">No logs found</p>
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {filteredLogs.map((log) => (
                  <div key={log.id}>
                    {/* Log Row */}
                    <Button
                      variant="ghost"
                      onClick={() =>
                        setExpandedLog(expandedLog === log.id ? null : log.id)
                      }
                      className="w-full p-4 h-auto hover:bg-muted rounded-lg border border-border transition text-left flex items-center justify-between"
                    >
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-1">
                          <p className="font-semibold text-foreground">{log.operation}</p>
                          <Badge variant={getStatusVariant(log.status)}>
                            {log.status === 'success'
                              ? '✅ Success'
                              : log.status === 'error'
                              ? '❌ Error'
                              : '⏳ Pending'}
                          </Badge>
                        </div>
                        <p className="text-sm text-muted-foreground">{log.message}</p>
                        <p className="text-xs text-muted-foreground mt-1">🕐 {log.timestamp}</p>
                      </div>
                      {expandedLog === log.id ? (
                        <ChevronDown className="h-4 w-4 text-muted-foreground ml-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-muted-foreground ml-4" />
                      )}
                    </Button>

                    {/* Expanded Details */}
                    {expandedLog === log.id && (
                      <div className="bg-muted text-muted-foreground p-4 rounded-b-lg font-mono text-xs border border-t-0 border-border animate-slide-down">
                        <p>{log.details}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Action Buttons */}
              <div className="mt-6 flex gap-3">
                <Button variant="secondary" size="sm">
                  <Download className="h-4 w-4 mr-2" />
                  Export Selected
                </Button>
                <Button variant="destructive" size="sm">
                  <Trash2 className="h-4 w-4 mr-2" />
                  Clear Old Logs
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default Logs
