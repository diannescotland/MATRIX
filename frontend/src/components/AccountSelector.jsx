import React from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

function AccountSelector({ accounts, selectedAccount, onAccountChange }) {
  if (!accounts || accounts.length === 0) {
    return (
      <Card className="border-yellow-500/50 bg-yellow-500/10">
        <CardContent className="pt-4">
          <p className="text-yellow-500 text-sm">
            ⚠️ No accounts found. Please add an account in the Accounts page.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-4">
          <div className="flex-shrink-0">
            <span className="text-2xl">📱</span>
          </div>
          <div className="flex-1">
            <label className="text-sm font-medium text-muted-foreground mb-2 block">
              Select Account
            </label>
            <Select
              value={selectedAccount?.phone || ''}
              onValueChange={(phone) => {
                const account = accounts.find(acc => acc.phone === phone)
                if (account && onAccountChange) {
                  onAccountChange(account)
                }
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Choose an account...">
                  {selectedAccount && (
                    <div className="flex items-center gap-2">
                      <span className="font-medium">
                        {selectedAccount.name || selectedAccount.phone}
                      </span>
                      {selectedAccount.is_default === 1 && (
                        <Badge variant="success" className="text-xs">Default</Badge>
                      )}
                      <span className="text-muted-foreground text-sm">
                        ({selectedAccount.phone})
                      </span>
                    </div>
                  )}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {accounts.map((account) => (
                  <SelectItem key={account.phone} value={account.phone}>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">
                        {account.name || account.phone}
                      </span>
                      {account.is_default === 1 && (
                        <Badge variant="success" className="text-xs ml-2">Default</Badge>
                      )}
                      <span className="text-muted-foreground text-sm">
                        ({account.phone})
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {selectedAccount && (
            <div className="flex-shrink-0 text-right">
              <p className="text-xs text-muted-foreground">Status</p>
              <Badge
                variant={selectedAccount.status === 'active' ? 'success' : 'warning'}
                className="mt-1"
              >
                {selectedAccount.status || 'active'}
              </Badge>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default AccountSelector
