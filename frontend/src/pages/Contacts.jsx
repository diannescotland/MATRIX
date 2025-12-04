import React, { useState, useEffect, useContext } from 'react'
import { getContacts } from '../services/api'
import { AccountContext } from '../context/AccountContext'
import LoadingSpinner from '../components/LoadingSpinner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertCircle, RefreshCw } from 'lucide-react'

function Contacts({ isConnected }) {
  const { selectedAccounts } = useContext(AccountContext)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState('all')
  const [filterStatus, setFilterStatus] = useState('all')
  const [contacts, setContacts] = useState([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [backupFile, setBackupFile] = useState('')

  useEffect(() => {
    if (isConnected) {
      fetchContacts()
    } else {
      setLoading(false)
    }
  }, [isConnected, filterType, filterStatus, selectedAccounts])

  const fetchContacts = async () => {
    setLoading(true)
    const phone = selectedAccounts.length === 1 ? selectedAccounts[0]?.phone : null
    const result = await getContacts({
      phone,
      type: filterType,
      status: filterStatus,
      search: searchQuery,
      limit: 200
    })
    if (result.success) {
      setContacts(result.data?.contacts || [])
      setTotal(result.data?.total || 0)
      setBackupFile(result.data?.backup_file || '')
    }
    setLoading(false)
  }

  const handleSearch = () => {
    fetchContacts()
  }

  const getTypeIcon = (type) => {
    if (type === 'dev') return '💻'
    if (type === 'kol') return '📢'
    return ''
  }

  const getStatusBadge = (status) => {
    if (status === 'blue') {
      return <Badge variant="secondary" className="bg-blue-500/20 text-blue-400">🔵 No Reply</Badge>
    }
    if (status === 'yellow') {
      return <Badge variant="secondary" className="bg-yellow-500/20 text-yellow-400">🟡 Replied</Badge>
    }
    return <Badge variant="outline">Unknown</Badge>
  }

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
      {/* Filters */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>🔍 Search & Filter</span>
            <Button variant="outline" size="sm" onClick={fetchContacts}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Search */}
          <div className="flex gap-2">
            <Input
              type="text"
              placeholder="Search by name, chain, or handle..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="flex-1"
            />
            <Button onClick={handleSearch}>Search</Button>
          </div>

          {/* Filter Buttons */}
          <div className="flex flex-wrap gap-2">
            <div className="flex gap-2">
              <Button
                variant={filterType === 'all' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterType('all')}
              >
                All
              </Button>
              <Button
                variant={filterType === 'dev' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterType('dev')}
              >
                💻 Developers
              </Button>
              <Button
                variant={filterType === 'kol' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterType('kol')}
              >
                📢 KOLs
              </Button>
            </div>

            <div className="flex gap-2">
              <Button
                variant={filterStatus === 'all' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterStatus('all')}
              >
                All Status
              </Button>
              <Button
                variant={filterStatus === 'blue' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterStatus('blue')}
              >
                🔵 Blue Only
              </Button>
              <Button
                variant={filterStatus === 'yellow' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setFilterStatus('yellow')}
              >
                🟡 Yellow Only
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Contacts Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>📋 Contacts</span>
            <div className="text-sm font-normal text-muted-foreground">
              {total > 0 && (
                <>
                  Showing {contacts.length} of {total} contacts
                  {backupFile && <span className="ml-2">({backupFile})</span>}
                </>
              )}
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <LoadingSpinner message="Loading contacts..." />
          ) : contacts.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-muted-foreground font-medium">No contacts found</p>
              <p className="text-muted-foreground text-sm mt-1">
                {total === 0
                  ? 'Create a backup first to view contacts here'
                  : 'Try adjusting your filters'}
              </p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Username</TableHead>
                    <TableHead>Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {contacts.map((contact) => (
                    <TableRow key={contact.id}>
                      <TableCell className="font-medium">{contact.name}</TableCell>
                      <TableCell>
                        <span className="text-lg">{getTypeIcon(contact.type)}</span>
                        <span className="ml-1 text-sm capitalize">{contact.type || '-'}</span>
                      </TableCell>
                      <TableCell>{getStatusBadge(contact.status)}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {contact.username ? `@${contact.username}` : '-'}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground max-w-xs truncate">
                        {contact.details || '-'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default Contacts
