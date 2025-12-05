import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import LoadingSpinner from '../components/LoadingSpinner'
import MultiAccountSelector from '../components/MultiAccountSelector'
import { useAccounts } from '../context/AccountContext'
import { getStats, getStatus, backupContacts, invalidateCache } from '../services/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Users,
  Code,
  Megaphone,
  TrendingUp,
  ArrowRight,
  Search,
  FolderOpen,
  Upload,
  Download,
  CheckCircle,
  Clock,
  AlertCircle,
  Sparkles,
  Activity
} from 'lucide-react'

function Dashboard({ isConnected }) {
  const [stats, setStats] = useState(null)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [backupLoading, setBackupLoading] = useState(false)
  const [backupMessage, setBackupMessage] = useState(null)

  // Use shared account context
  const {
    accounts,
    selectedAccounts,
    setSelectedAccounts,
    getPhoneList,
    hasSelection
  } = useAccounts()

  // Invalidate cache when account selection changes to ensure fresh data
  useEffect(() => {
    invalidateCache() // Clear all cached stats to prevent stale data
  }, [selectedAccounts])

  // Fetch stats and status when accounts selection changes
  useEffect(() => {
    if (hasSelection && isConnected) {
      fetchData()
      const interval = setInterval(fetchData, 10000) // Refresh every 10 seconds
      return () => clearInterval(interval)
    }
  }, [selectedAccounts, isConnected])

  const fetchData = async () => {
    if (!hasSelection) return

    try {
      setError(null)
      setLoading(true)

      // Use context helper to get comma-separated phone list
      const phones = getPhoneList()

      const [statsRes, statusRes] = await Promise.all([
        getStats(phones),
        getStatus()
      ])

      if (statsRes.success) {
        setStats(statsRes.data)
        // Check if any account has no backup using has_backup flag
        if (statsRes.data?.accounts) {
          const missingBackups = statsRes.data.accounts.filter(acc => !acc.has_backup)
          if (missingBackups.length > 0) {
            const names = missingBackups.map(acc => acc.phone).join(', ')
            setError(`No backup yet for: ${names}. Run backup to see contacts.`)
          }
        } else if (statsRes.data?.has_backup === false) {
          // Single account with no backup
          setError('No backup yet - run backup to see contacts.')
        }
      } else {
        // Handle errors (400 = phone required, other errors)
        const errorMsg = statsRes.error?.response?.data?.error ||
                        statsRes.error?.message ||
                        'Failed to load stats'
        setError(errorMsg)
        // Set empty stats so UI doesn't break
        setStats({
          total_contacts: 0,
          dev_contacts: { total: 0, blue: 0, yellow: 0 },
          kol_contacts: { total: 0, blue: 0, yellow: 0 }
        })
      }

      if (statusRes.success) {
        setStatus(statusRes.data)
      }
    } catch (err) {
      setError('Failed to fetch dashboard data')
    } finally {
      setLoading(false)
    }
  }

  const handleBackup = async () => {
    try {
      setBackupLoading(true)
      setBackupMessage(null)

      // Get phone from selected account (backup first selected account)
      const phone = selectedAccounts[0]?.phone
      if (!phone) {
        setBackupMessage({
          type: 'error',
          text: 'No account selected. Please select an account first.'
        })
        setBackupLoading(false)
        return
      }

      const result = await backupContacts(phone)

      if (result.success) {
        const filename = result.data.filename
        const downloadUrl = `http://localhost:5000${result.data.download_url}`

        setBackupMessage({
          type: 'success',
          text: `Backup successful! ${filename}`,
          downloadUrl
        })

        // Auto-download the file
        const link = document.createElement('a')
        link.href = downloadUrl
        link.download = filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      } else {
        setBackupMessage({
          type: 'error',
          text: `Backup failed: ${result.error?.message || 'Unknown error'}`
        })
      }
    } catch (err) {
      setBackupMessage({
        type: 'error',
        text: `Backup failed: ${err.message}`
      })
    } finally {
      setBackupLoading(false)
    }
  }

  if (!isConnected) {
    return (
      <div className="p-6">
        <Card className="border-red-500/30 bg-red-500/5">
          <CardHeader>
            <CardTitle className="text-red-500 flex items-center gap-2">
              <AlertCircle className="h-5 w-5" />
              API Connection Failed
            </CardTitle>
            <CardDescription className="text-red-400/80">
              Cannot connect to the MATRIX API. Make sure the Flask server is running on http://localhost:5000
            </CardDescription>
          </CardHeader>
          <CardContent>
            <code className="text-sm bg-muted px-3 py-1.5 rounded-lg inline-block">python api_server.py</code>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[60vh]">
        <LoadingSpinner message="Loading dashboard..." />
      </div>
    )
  }

  // Calculate totals
  const totalDevs = (stats?.dev_contacts?.blue || 0) + (stats?.dev_contacts?.yellow || 0)
  const totalKols = (stats?.kol_contacts?.blue || 0) + (stats?.kol_contacts?.yellow || 0)
  const totalContacts = totalDevs + totalKols
  const totalReplied = (stats?.dev_contacts?.yellow || 0) + (stats?.kol_contacts?.yellow || 0)
  const replyRate = totalContacts > 0 ? Math.round((totalReplied / totalContacts) * 100) : 0

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      {/* Account Selector */}
      <div className="flex items-center justify-between">
        <MultiAccountSelector
          accounts={accounts}
          selectedAccounts={selectedAccounts}
          onSelectionChange={setSelectedAccounts}
        />
        {selectedAccounts.length > 1 && stats?.account_count && (
          <Badge variant="outline" className="badge-info px-3 py-1">
            <Activity className="h-3 w-3 mr-1.5" />
            Aggregated from {stats.account_count} accounts
          </Badge>
        )}
      </div>

      {error && (
        <Card className="border-yellow-500/30 bg-yellow-500/5">
          <CardContent className="py-4 flex items-center gap-3">
            <AlertCircle className="h-5 w-5 text-yellow-500 shrink-0" />
            <p className="text-yellow-500 text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Overview Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Blue Developers */}
        <Card className="stat-card-blue border hover-lift cursor-default group">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium text-blue-400">Blue Developers</p>
                <p className="text-4xl font-bold text-foreground">
                  {stats?.dev_contacts?.blue || 0}
                </p>
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Awaiting reply
                </p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-blue-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Code className="h-6 w-6 text-blue-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Yellow Developers */}
        <Card className="stat-card-yellow border hover-lift cursor-default group">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium text-yellow-400">Yellow Developers</p>
                <p className="text-4xl font-bold text-foreground">
                  {stats?.dev_contacts?.yellow || 0}
                </p>
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <CheckCircle className="h-3 w-3 text-green-500" />
                  Replied
                </p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-yellow-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Code className="h-6 w-6 text-yellow-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Blue KOLs */}
        <Card className="stat-card-purple border hover-lift cursor-default group">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium text-purple-400">Blue KOLs</p>
                <p className="text-4xl font-bold text-foreground">
                  {stats?.kol_contacts?.blue || 0}
                </p>
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Awaiting reply
                </p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-purple-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Megaphone className="h-6 w-6 text-purple-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Yellow KOLs */}
        <Card className="stat-card-orange border hover-lift cursor-default group">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium text-orange-400">Yellow KOLs</p>
                <p className="text-4xl font-bold text-foreground">
                  {stats?.kol_contacts?.yellow || 0}
                </p>
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <CheckCircle className="h-3 w-3 text-green-500" />
                  Replied
                </p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-orange-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Megaphone className="h-6 w-6 text-orange-400" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Summary Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Total Contacts */}
        <Card className="border-primary/20 bg-gradient-to-br from-primary/10 to-transparent">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-muted-foreground">Total Contacts</p>
                <p className="text-3xl font-bold text-foreground mt-1">{totalContacts}</p>
              </div>
              <div className="w-14 h-14 rounded-2xl gradient-primary flex items-center justify-center shadow-glow glow-blue">
                <Users className="h-7 w-7 text-white" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Reply Rate */}
        <Card className="border-green-500/20 bg-gradient-to-br from-green-500/10 to-transparent">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-muted-foreground">Reply Rate</p>
                <p className="text-3xl font-bold text-foreground mt-1">{replyRate}%</p>
              </div>
              <div className="w-14 h-14 rounded-2xl bg-green-500 flex items-center justify-center shadow-glow glow-green">
                <TrendingUp className="h-7 w-7 text-white" />
              </div>
            </div>
            <div className="mt-3 h-2 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-green-500 rounded-full transition-all duration-500"
                style={{ width: `${replyRate}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Total Replied */}
        <Card className="border-yellow-500/20 bg-gradient-to-br from-yellow-500/10 to-transparent">
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-muted-foreground">Total Replied</p>
                <p className="text-3xl font-bold text-foreground mt-1">{totalReplied}</p>
              </div>
              <div className="w-14 h-14 rounded-2xl bg-yellow-500 flex items-center justify-center shadow-glow glow-yellow">
                <Sparkles className="h-7 w-7 text-white" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Per-Account Breakdown (only for multi-account view) */}
      {selectedAccounts.length > 1 && stats?.accounts && stats.accounts.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2">
              <Users className="h-5 w-5 text-primary" />
              Per-Account Breakdown
            </CardTitle>
            <CardDescription>
              Individual statistics for each selected account
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.accounts.map((accountData, idx) => {
              const accountInfo = selectedAccounts.find(acc => acc.phone === accountData.phone)
              const accountStats = accountData.stats
              const hasBackup = accountData.has_backup !== false && accountStats?.has_backup !== false

              if (!hasBackup) {
                return (
                  <div key={idx} className="p-4 bg-yellow-500/5 rounded-xl border border-yellow-500/20">
                    <div className="flex items-center gap-3">
                      <Download className="h-5 w-5 text-yellow-500" />
                      <div>
                        <p className="font-medium text-foreground">
                          {accountInfo?.name || accountData.phone}
                        </p>
                        <p className="text-sm text-yellow-400">
                          No backup yet - run backup to see contacts
                        </p>
                      </div>
                    </div>
                  </div>
                )
              }

              return (
                <div key={idx} className="p-4 bg-accent/30 rounded-xl border border-border/50">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <p className="font-medium text-foreground">
                        {accountInfo?.name || accountData.phone}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {accountData.phone}
                      </p>
                    </div>
                    <Badge variant="outline">
                      {accountStats.total_contacts} total
                    </Badge>
                  </div>

                  <div className="grid grid-cols-4 gap-2">
                    <div className="p-2.5 bg-blue-500/10 rounded-lg text-center">
                      <p className="text-xs text-blue-400 font-medium">Blue Devs</p>
                      <p className="text-lg font-bold text-foreground">
                        {accountStats.dev_contacts?.blue || 0}
                      </p>
                    </div>
                    <div className="p-2.5 bg-yellow-500/10 rounded-lg text-center">
                      <p className="text-xs text-yellow-400 font-medium">Yellow Devs</p>
                      <p className="text-lg font-bold text-foreground">
                        {accountStats.dev_contacts?.yellow || 0}
                      </p>
                    </div>
                    <div className="p-2.5 bg-purple-500/10 rounded-lg text-center">
                      <p className="text-xs text-purple-400 font-medium">Blue KOLs</p>
                      <p className="text-lg font-bold text-foreground">
                        {accountStats.kol_contacts?.blue || 0}
                      </p>
                    </div>
                    <div className="p-2.5 bg-orange-500/10 rounded-lg text-center">
                      <p className="text-xs text-orange-400 font-medium">Yellow KOLs</p>
                      <p className="text-lg font-bold text-foreground">
                        {accountStats.kol_contacts?.yellow || 0}
                      </p>
                    </div>
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}

      {/* Quick Actions */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            Quick Actions
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            <Link to="/operations" className="block">
              <Button className="w-full h-auto py-4 flex flex-col gap-2 hover-scale" variant="outline">
                <Search className="h-5 w-5" />
                <span>Scan & Update</span>
              </Button>
            </Link>
            <Link to="/operations" className="block">
              <Button className="w-full h-auto py-4 flex flex-col gap-2 hover-scale" variant="outline">
                <FolderOpen className="h-5 w-5" />
                <span>Organize Folders</span>
              </Button>
            </Link>
            <Link to="/import" className="block">
              <Button className="w-full h-auto py-4 flex flex-col gap-2 hover-scale" variant="outline">
                <Upload className="h-5 w-5" />
                <span>Import Contacts</span>
              </Button>
            </Link>
            <Button
              onClick={handleBackup}
              disabled={backupLoading}
              className="w-full h-auto py-4 flex flex-col gap-2 hover-scale"
              variant="outline"
            >
              <Download className={`h-5 w-5 ${backupLoading ? 'animate-pulse' : ''}`} />
              <span>{backupLoading ? 'Backing up...' : 'Backup Contacts'}</span>
            </Button>
          </div>

          {backupMessage && (
            <div className={`flex items-start gap-3 p-4 rounded-xl ${
              backupMessage.type === 'success'
                ? 'bg-green-500/10 border border-green-500/20'
                : 'bg-red-500/10 border border-red-500/20'
            }`}>
              {backupMessage.type === 'success' ? (
                <CheckCircle className="h-5 w-5 text-green-500 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
              )}
              <div className="flex-1">
                <p className={`text-sm font-medium ${
                  backupMessage.type === 'success' ? 'text-green-500' : 'text-red-500'
                }`}>
                  {backupMessage.text}
                </p>
                {backupMessage.downloadUrl && (
                  <a
                    href={backupMessage.downloadUrl}
                    download
                    className="text-xs text-primary hover:underline mt-1 inline-block"
                  >
                    Download again
                  </a>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent Operations */}
      {status?.recent_operations && status.recent_operations.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-5 w-5 text-primary" />
                Recent Operations
              </CardTitle>
              <Link to="/logs">
                <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
                  View all
                  <ArrowRight className="h-4 w-4 ml-1" />
                </Button>
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {status.recent_operations.slice(0, 5).map((op, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between p-3 rounded-xl bg-accent/30 border border-border/50 hover:bg-accent/50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                      op.status === 'success' ? 'bg-green-500/20' : 'bg-yellow-500/20'
                    }`}>
                      {op.status === 'success' ? (
                        <CheckCircle className="h-4 w-4 text-green-500" />
                      ) : (
                        <Clock className="h-4 w-4 text-yellow-500" />
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-foreground text-sm">{op.operation}</p>
                      <p className="text-xs text-muted-foreground">{op.timestamp}</p>
                    </div>
                  </div>
                  <Badge variant="outline" className={
                    op.status === 'success' ? 'badge-success' : 'badge-warning'
                  }>
                    {op.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export default Dashboard
