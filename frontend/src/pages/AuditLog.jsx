import React, { useState, useEffect } from 'react'
import { getAuditLog } from '../services/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Label } from '@/components/ui/label'
import { AlertCircle, RefreshCw } from 'lucide-react'
import LoadingSpinner from '../components/LoadingSpinner'

export default function AuditLog() {
  const [logs, setLogs] = useState([])
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchLogs()
  }, [filter])

  const fetchLogs = async () => {
    setLoading(true)
    setError(null)

    const params = filter !== 'all' ? { type: filter } : {}
    const result = await getAuditLog(params)

    if (result.success) {
      setLogs(result.data.logs || [])
    } else {
      setError(result.error?.message || 'Failed to load audit logs')
    }

    setLoading(false)
  }

  const formatDate = (timestamp) => {
    return new Date(timestamp).toLocaleString()
  }

  const getSuccessRateVariant = (rate) => {
    if (rate >= 80) return 'success'
    if (rate >= 50) return 'warning'
    return 'destructive'
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold mb-2">Operation Audit Log</h1>
        <p className="text-muted-foreground">View history of all operations and their success rates</p>
      </div>

      <Card className="mb-6">
        <CardContent className="pt-6">
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <Label htmlFor="filter-select">Filter by Operation</Label>
              <Select value={filter} onValueChange={setFilter}>
                <SelectTrigger id="filter-select">
                  <SelectValue placeholder="Select operation type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Operations</SelectItem>
                  <SelectItem value="import_devs">Dev Imports</SelectItem>
                  <SelectItem value="import_kols">KOL Imports</SelectItem>
                  <SelectItem value="scan_replies">Reply Scans</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button onClick={fetchLogs} variant="outline">
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="flex justify-center items-center py-12">
              <LoadingSpinner message="Loading audit logs..." />
            </div>
          ) : logs.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              No audit logs found.
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Operation</TableHead>
                    <TableHead>Account</TableHead>
                    <TableHead className="text-center">Success/Total</TableHead>
                    <TableHead className="text-center">Success Rate</TableHead>
                    <TableHead className="text-center">Duration</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((log) => {
                    const total = (log.success_count || 0) + (log.failed_count || 0)
                    const rate = total > 0 ? ((log.success_count / total) * 100).toFixed(1) : 0

                    return (
                      <TableRow key={log.id}>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDate(log.timestamp)}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{log.operation_type}</Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {log.account_phone || 'N/A'}
                        </TableCell>
                        <TableCell className="text-center text-sm">
                          {log.success_count}/{total}
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge variant={getSuccessRateVariant(parseFloat(rate))}>
                            {rate}%
                          </Badge>
                        </TableCell>
                        <TableCell className="text-center text-sm text-muted-foreground">
                          {log.duration_seconds ? `${log.duration_seconds.toFixed(1)}s` : 'N/A'}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
              <div className="mt-4 text-sm text-muted-foreground text-right">
                Showing {logs.length} audit log entries
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
