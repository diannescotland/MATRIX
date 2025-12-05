import React, { useState, useEffect } from 'react'
import {
  getAccounts,
  addAccount,
  deleteAccount,
  updateAccountStatus,
  updateAccountProxy,
  sendAuthCode,
  verifyAuthCode,
  verifyAuthPassword
} from '../services/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertCircle,
  Plus,
  Trash2,
  RefreshCw,
  CheckCircle,
  XCircle,
  Smartphone,
  Key,
  Lock,
  ArrowRight,
  Sparkles,
  Phone,
  Hash,
  FileText,
  Globe,
  Edit2
} from 'lucide-react'
import LoadingSpinner from '../components/LoadingSpinner'

export default function Accounts() {
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [authStep, setAuthStep] = useState('phone') // 'phone', 'code', 'password', 'success'
  const [formData, setFormData] = useState({
    phone: '',
    name: '',
    api_id: '',
    api_hash: '',
    notes: '',
    proxy: '',
  })
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [authError, setAuthError] = useState(null)
  const [authSuccess, setAuthSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  // Proxy editing state
  const [proxyDialogOpen, setProxyDialogOpen] = useState(false)
  const [editingAccount, setEditingAccount] = useState(null)
  const [proxyInput, setProxyInput] = useState('')
  const [proxySubmitting, setProxySubmitting] = useState(false)
  const [proxyError, setProxyError] = useState(null)
  const [proxySuccess, setProxySuccess] = useState(false)

  useEffect(() => {
    fetchAccounts()
  }, [])

  const fetchAccounts = async () => {
    setLoading(true)
    const response = await getAccounts(false) // Don't use cache
    if (response.success) {
      setAccounts(response.data.accounts || [])
    }
    setLoading(false)
  }

  const handleFormChange = (field, value) => {
    setFormData({ ...formData, [field]: value })
  }

  const handleSendCode = async () => {
    setAuthError(null)
    setSubmitting(true)

    // Validate
    if (!formData.phone || !formData.api_id || !formData.api_hash) {
      setAuthError('Please fill in all required fields')
      setSubmitting(false)
      return
    }

    // Validate proxy format if provided
    if (formData.proxy && !formData.proxy.match(/^(http|https|socks4|socks5):\/\/.+:\d+$/)) {
      setAuthError('Invalid proxy format. Use: http://ip:port')
      setSubmitting(false)
      return
    }

    const response = await sendAuthCode(formData.phone, formData.api_id, formData.api_hash, formData.proxy)
    setSubmitting(false)

    if (response.success) {
      setAuthStep('code')
    } else {
      setAuthError(response.error?.message || 'Failed to send code')
    }
  }

  const handleVerifyCode = async () => {
    setAuthError(null)
    setSubmitting(true)

    if (!code) {
      setAuthError('Please enter the verification code')
      setSubmitting(false)
      return
    }

    const response = await verifyAuthCode(formData.phone, code)
    setSubmitting(false)

    if (response.success) {
      if (response.data.requires_password) {
        setAuthStep('password')
      } else {
        // Account added successfully
        await handleAccountAdded()
      }
    } else {
      setAuthError(response.error?.message || 'Invalid code')
    }
  }

  const handleVerifyPassword = async () => {
    setAuthError(null)
    setSubmitting(true)

    if (!password) {
      setAuthError('Please enter your 2FA password')
      setSubmitting(false)
      return
    }

    const response = await verifyAuthPassword(formData.phone, password)
    setSubmitting(false)

    if (response.success) {
      await handleAccountAdded()
    } else {
      setAuthError(response.error?.message || 'Invalid password')
    }
  }

  const handleAccountAdded = async () => {
    // The backend already saves the account during authentication via _save_session_to_database()
    // This call is just to update any additional metadata (name, notes) if the user provided them
    // We don't treat "already exists" as an error since that's expected

    const accountData = {
      phone: formData.phone,
      name: formData.name || formData.phone,
      api_id: formData.api_id,
      api_hash: formData.api_hash,
      notes: formData.notes || '',
      proxy: formData.proxy || null,
      status: 'active'
    }

    const response = await addAccount(accountData)
    // Success OR "account already exists" are both OK
    // (backend saves account during auth, so it may already exist)
    const isSuccess = response.success ||
      (response.error?.response?.data?.error === 'Account already exists') ||
      (response.error?.message?.includes('already exists'))

    if (isSuccess) {
      setAuthSuccess(true)
      setAuthStep('success')
      setTimeout(() => {
        resetDialog()
        fetchAccounts()
      }, 2000)
    } else {
      setAuthError(response.error?.response?.data?.error || response.error?.message || 'Failed to save account')
    }
  }

  const resetDialog = () => {
    setDialogOpen(false)
    setAuthStep('phone')
    setFormData({
      phone: '',
      name: '',
      api_id: '',
      api_hash: '',
      notes: '',
      proxy: '',
    })
    setCode('')
    setPassword('')
    setAuthError(null)
    setAuthSuccess(false)
  }

  // Proxy editing handlers
  const openProxyDialog = (account) => {
    setEditingAccount(account)
    setProxyInput(account.proxy || '')
    setProxyError(null)
    setProxySuccess(false)
    setProxyDialogOpen(true)
  }

  const handleUpdateProxy = async () => {
    setProxyError(null)
    setProxySubmitting(true)

    // Validate proxy format if provided
    if (proxyInput && !proxyInput.match(/^(http|https|socks4|socks5):\/\/.+:\d+$/)) {
      setProxyError('Invalid proxy format. Use: http://ip:port')
      setProxySubmitting(false)
      return
    }

    const response = await updateAccountProxy(editingAccount.phone, proxyInput || null)
    setProxySubmitting(false)

    if (response.success) {
      setProxySuccess(true)
      setTimeout(() => {
        setProxyDialogOpen(false)
        setProxySuccess(false)
        fetchAccounts()
      }, 1500)
    } else {
      setProxyError(response.error?.response?.data?.error || response.error?.message || 'Failed to update proxy')
    }
  }

  const handleDeleteAccount = async (phone) => {
    if (!confirm(`Are you sure you want to delete account ${phone}?`)) {
      return
    }

    const response = await deleteAccount(phone)
    if (response.success) {
      fetchAccounts()
    } else {
      alert(`Failed to delete account: ${response.error?.message}`)
    }
  }

  const handleToggleStatus = async (phone, currentStatus) => {
    const newStatus = currentStatus === 'active' ? 'inactive' : 'active'
    const response = await updateAccountStatus(phone, newStatus)
    if (response.success) {
      fetchAccounts()
    } else {
      alert(`Failed to update status: ${response.error?.message}`)
    }
  }

  const getStepNumber = () => {
    const steps = { phone: 1, code: 2, password: 3, success: 4 }
    return steps[authStep]
  }

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
            <Smartphone className="h-6 w-6 text-primary" />
            Telegram Accounts
          </h1>
          <p className="text-muted-foreground mt-1">Manage your Telegram accounts and authentication</p>
        </div>
        <div className="flex gap-3">
          <Button onClick={fetchAccounts} variant="outline" className="hover-scale">
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button className="hover-scale">
                <Plus className="h-4 w-4 mr-2" />
                Add Account
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-lg">
              {/* Step Indicator */}
              <div className="flex items-center justify-center gap-2 mb-4">
                {[1, 2, 3, 4].map((step) => (
                  <div key={step} className="flex items-center">
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold transition-colors ${
                      getStepNumber() >= step
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground'
                    }`}>
                      {step === 4 && authSuccess ? (
                        <CheckCircle className="h-4 w-4" />
                      ) : (
                        step
                      )}
                    </div>
                    {step < 4 && (
                      <div className={`w-8 h-0.5 ${getStepNumber() > step ? 'bg-primary' : 'bg-muted'}`} />
                    )}
                  </div>
                ))}
              </div>

              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {authStep === 'phone' && <><Smartphone className="h-5 w-5 text-primary" /> Add Telegram Account</>}
                  {authStep === 'code' && <><Key className="h-5 w-5 text-primary" /> Enter Verification Code</>}
                  {authStep === 'password' && <><Lock className="h-5 w-5 text-primary" /> Enter 2FA Password</>}
                  {authStep === 'success' && <><Sparkles className="h-5 w-5 text-green-500" /> Account Added</>}
                </DialogTitle>
                <DialogDescription>
                  {authStep === 'phone' && 'Enter your Telegram account credentials to get started'}
                  {authStep === 'code' && 'Check your Telegram app for the verification code'}
                  {authStep === 'password' && 'Enter your two-factor authentication password'}
                  {authStep === 'success' && 'Your account has been added and is ready to use'}
                </DialogDescription>
              </DialogHeader>

              {authError && (
                <Alert variant="destructive" className="border-red-500/30 bg-red-500/5">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>{authError}</AlertDescription>
                </Alert>
              )}

              {authSuccess && (
                <Alert className="border-green-500/30 bg-green-500/5">
                  <CheckCircle className="h-4 w-4 text-green-500" />
                  <AlertDescription className="text-green-500">
                    Account added successfully! Redirecting...
                  </AlertDescription>
                </Alert>
              )}

              {authStep === 'phone' && (
                <div className="space-y-4 py-2">
                  <div className="space-y-2">
                    <Label htmlFor="phone" className="flex items-center gap-2">
                      <Phone className="h-4 w-4 text-muted-foreground" />
                      Phone Number *
                    </Label>
                    <Input
                      id="phone"
                      placeholder="+1234567890"
                      value={formData.phone}
                      onChange={(e) => handleFormChange('phone', e.target.value)}
                      className="h-11"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="name" className="flex items-center gap-2">
                      <Smartphone className="h-4 w-4 text-muted-foreground" />
                      Account Name
                    </Label>
                    <Input
                      id="name"
                      placeholder="My Account"
                      value={formData.name}
                      onChange={(e) => handleFormChange('name', e.target.value)}
                      className="h-11"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="api_id" className="flex items-center gap-2">
                        <Hash className="h-4 w-4 text-muted-foreground" />
                        API ID *
                      </Label>
                      <Input
                        id="api_id"
                        placeholder="12345678"
                        value={formData.api_id}
                        onChange={(e) => handleFormChange('api_id', e.target.value)}
                        className="h-11"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="api_hash" className="flex items-center gap-2">
                        <Key className="h-4 w-4 text-muted-foreground" />
                        API Hash *
                      </Label>
                      <Input
                        id="api_hash"
                        placeholder="abcd1234..."
                        value={formData.api_hash}
                        onChange={(e) => handleFormChange('api_hash', e.target.value)}
                        className="h-11"
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="proxy" className="flex items-center gap-2">
                      <Globe className="h-4 w-4 text-muted-foreground" />
                      Proxy (Optional)
                    </Label>
                    <Input
                      id="proxy"
                      placeholder="http://ip:port"
                      value={formData.proxy}
                      onChange={(e) => handleFormChange('proxy', e.target.value)}
                      className="h-11 font-mono"
                    />
                    <p className="text-xs text-muted-foreground">
                      Format: http://ip:port or socks5://ip:port
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="notes" className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-muted-foreground" />
                      Notes (Optional)
                    </Label>
                    <Textarea
                      id="notes"
                      placeholder="Optional notes about this account"
                      value={formData.notes}
                      onChange={(e) => handleFormChange('notes', e.target.value)}
                      rows={2}
                    />
                  </div>
                </div>
              )}

              {authStep === 'code' && (
                <div className="space-y-4 py-2">
                  <Alert className="border-primary/30 bg-primary/5">
                    <AlertDescription className="text-muted-foreground">
                      A verification code has been sent to your Telegram app on the device linked to <strong className="text-foreground">{formData.phone}</strong>
                    </AlertDescription>
                  </Alert>
                  <div className="space-y-2">
                    <Label htmlFor="code" className="flex items-center gap-2">
                      <Key className="h-4 w-4 text-muted-foreground" />
                      Verification Code *
                    </Label>
                    <Input
                      id="code"
                      placeholder="12345"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      maxLength={5}
                      className="h-12 text-center text-2xl tracking-widest font-mono"
                    />
                  </div>
                </div>
              )}

              {authStep === 'password' && (
                <div className="space-y-4 py-2">
                  <Alert className="border-orange-500/30 bg-orange-500/5">
                    <Lock className="h-4 w-4 text-orange-500" />
                    <AlertDescription className="text-muted-foreground">
                      This account has two-factor authentication enabled. Please enter your password.
                    </AlertDescription>
                  </Alert>
                  <div className="space-y-2">
                    <Label htmlFor="password" className="flex items-center gap-2">
                      <Lock className="h-4 w-4 text-muted-foreground" />
                      2FA Password *
                    </Label>
                    <Input
                      id="password"
                      type="password"
                      placeholder="Enter your 2FA password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="h-11"
                    />
                  </div>
                </div>
              )}

              {authStep === 'success' && (
                <div className="py-8 text-center">
                  <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center mx-auto mb-4">
                    <CheckCircle className="h-8 w-8 text-green-500" />
                  </div>
                  <p className="text-lg font-semibold text-foreground">Account Added Successfully!</p>
                  <p className="text-sm text-muted-foreground mt-1">Your account is now ready to use</p>
                </div>
              )}

              <DialogFooter className="gap-2">
                {authStep === 'phone' && (
                  <>
                    <Button variant="outline" onClick={resetDialog}>
                      Cancel
                    </Button>
                    <Button onClick={handleSendCode} disabled={submitting} className="hover-scale">
                      {submitting ? 'Sending...' : 'Send Code'}
                      <ArrowRight className="h-4 w-4 ml-2" />
                    </Button>
                  </>
                )}
                {authStep === 'code' && (
                  <>
                    <Button variant="outline" onClick={() => setAuthStep('phone')}>
                      Back
                    </Button>
                    <Button onClick={handleVerifyCode} disabled={submitting} className="hover-scale">
                      {submitting ? 'Verifying...' : 'Verify Code'}
                      <ArrowRight className="h-4 w-4 ml-2" />
                    </Button>
                  </>
                )}
                {authStep === 'password' && (
                  <>
                    <Button variant="outline" onClick={() => setAuthStep('code')}>
                      Back
                    </Button>
                    <Button onClick={handleVerifyPassword} disabled={submitting} className="hover-scale">
                      {submitting ? 'Verifying...' : 'Verify Password'}
                      <ArrowRight className="h-4 w-4 ml-2" />
                    </Button>
                  </>
                )}
                {authStep === 'success' && (
                  <Button onClick={resetDialog} className="w-full hover-scale">
                    Close
                  </Button>
                )}
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex justify-center py-16">
              <LoadingSpinner message="Loading accounts..." />
            </div>
          ) : accounts.length === 0 ? (
            <div className="text-center py-16">
              <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mx-auto mb-4">
                <Smartphone className="h-8 w-8 text-muted-foreground" />
              </div>
              <p className="text-foreground font-medium">No accounts configured</p>
              <p className="text-muted-foreground text-sm mt-1">Click "Add Account" to get started</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-6">Account</TableHead>
                  <TableHead>Phone</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Proxy</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead className="text-right pr-6">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((account) => (
                  <TableRow key={account.phone} className="group">
                    <TableCell className="pl-6">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                          <Smartphone className="h-5 w-5 text-primary" />
                        </div>
                        <span className="font-medium text-foreground">
                          {account.name || account.phone}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-sm">
                      {account.phone}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          account.status === 'active'
                            ? 'badge-success'
                            : account.status === 'inactive'
                            ? 'bg-muted text-muted-foreground border-muted'
                            : 'badge-destructive'
                        }
                      >
                        {account.status === 'active' && <CheckCircle className="h-3 w-3 mr-1" />}
                        {account.status === 'inactive' && <XCircle className="h-3 w-3 mr-1" />}
                        {account.status === 'error' && <AlertCircle className="h-3 w-3 mr-1" />}
                        {account.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {account.proxy ? (
                          <span className="font-mono text-xs text-muted-foreground max-w-[150px] truncate" title={account.proxy}>
                            {account.proxy}
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">No proxy</span>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100"
                          onClick={() => openProxyDialog(account)}
                          title="Edit proxy"
                        >
                          <Edit2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-xs truncate text-muted-foreground text-sm">
                      {account.notes || '-'}
                    </TableCell>
                    <TableCell className="text-right pr-6">
                      <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleToggleStatus(account.phone, account.status)}
                          className="hover-scale"
                        >
                          {account.status === 'active' ? 'Deactivate' : 'Activate'}
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => handleDeleteAccount(account.phone)}
                          className="hover-scale"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Proxy Edit Dialog */}
      <Dialog open={proxyDialogOpen} onOpenChange={setProxyDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Globe className="h-5 w-5 text-primary" />
              Edit Proxy
            </DialogTitle>
            <DialogDescription>
              {editingAccount && (
                <>Update proxy for account <strong>{editingAccount.name || editingAccount.phone}</strong></>
              )}
            </DialogDescription>
          </DialogHeader>

          {proxyError && (
            <Alert variant="destructive" className="border-red-500/30 bg-red-500/5">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{proxyError}</AlertDescription>
            </Alert>
          )}

          {proxySuccess && (
            <Alert className="border-green-500/30 bg-green-500/5">
              <CheckCircle className="h-4 w-4 text-green-500" />
              <AlertDescription className="text-green-500">
                Proxy updated! Session invalidated - please re-authenticate.
              </AlertDescription>
            </Alert>
          )}

          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="edit-proxy" className="flex items-center gap-2">
                <Globe className="h-4 w-4 text-muted-foreground" />
                Proxy URL
              </Label>
              <Input
                id="edit-proxy"
                placeholder="http://ip:port"
                value={proxyInput}
                onChange={(e) => setProxyInput(e.target.value)}
                className="h-11 font-mono"
                disabled={proxySubmitting || proxySuccess}
              />
              <p className="text-xs text-muted-foreground">
                Format: http://ip:port or socks5://ip:port (leave empty to remove proxy)
              </p>
            </div>

            <Alert className="border-orange-500/30 bg-orange-500/5">
              <AlertCircle className="h-4 w-4 text-orange-500" />
              <AlertDescription className="text-muted-foreground text-sm">
                Changing proxy will <strong>log you out</strong> of this account. You will need to re-authenticate.
              </AlertDescription>
            </Alert>
          </div>

          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setProxyDialogOpen(false)}
              disabled={proxySubmitting}
            >
              Cancel
            </Button>
            <Button
              onClick={handleUpdateProxy}
              disabled={proxySubmitting || proxySuccess}
              className="hover-scale"
            >
              {proxySubmitting ? 'Updating...' : 'Update Proxy'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
