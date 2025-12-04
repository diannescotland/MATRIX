import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Check, Smartphone, CheckSquare, XSquare } from 'lucide-react'

function MultiAccountSelector({ accounts, selectedAccounts, onSelectionChange }) {
  if (!accounts || accounts.length === 0) {
    return (
      <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-yellow-500/10 border border-yellow-500/30">
        <Smartphone className="h-5 w-5 text-yellow-500" />
        <p className="text-yellow-500 text-sm">
          No accounts found. Please add an account in the Accounts page.
        </p>
      </div>
    )
  }

  const handleToggleAccount = (account) => {
    const isSelected = selectedAccounts.some(acc => acc.phone === account.phone)

    if (isSelected) {
      const newSelection = selectedAccounts.filter(acc => acc.phone !== account.phone)
      onSelectionChange(newSelection)
    } else {
      onSelectionChange([...selectedAccounts, account])
    }
  }

  const handleSelectAll = () => {
    onSelectionChange(accounts)
  }

  const handleDeselectAll = () => {
    onSelectionChange([])
  }

  const isAccountSelected = (account) => {
    return selectedAccounts.some(acc => acc.phone === account.phone)
  }

  const allSelected = accounts.length > 0 && selectedAccounts.length === accounts.length
  const noneSelected = selectedAccounts.length === 0

  const truncatePhone = (phone) => {
    if (!phone) return ''
    const cleanPhone = phone.replace(/\D/g, '')
    if (cleanPhone.length <= 4) return cleanPhone
    return `...${cleanPhone.slice(-4)}`
  }

  return (
    <div className="flex items-center gap-4">
      {/* Account chips */}
      <div className="flex flex-wrap gap-2">
        {accounts.map((account) => {
          const isSelected = isAccountSelected(account)
          const displayName = account.name || 'Account'
          const displayPhone = truncatePhone(account.phone)

          return (
            <button
              key={account.phone}
              type="button"
              onClick={() => handleToggleAccount(account)}
              className={`
                inline-flex items-center gap-2 px-4 py-2 rounded-xl border
                transition-all duration-200 active:scale-[0.97]
                ${isSelected
                  ? 'border-primary bg-primary text-primary-foreground shadow-glow glow-blue'
                  : 'border-border bg-card text-muted-foreground hover:border-primary/50 hover:bg-accent'
                }
              `}
            >
              {isSelected && (
                <Check className="w-4 h-4" />
              )}

              <Smartphone className={`w-4 h-4 ${isSelected ? '' : 'text-muted-foreground'}`} />

              <span className="font-medium text-sm max-w-[120px] truncate">
                {displayName}
              </span>

              <span className={`text-xs ${isSelected ? 'text-primary-foreground/70' : 'text-muted-foreground/60'}`}>
                ({displayPhone})
              </span>

              {account.is_default === 1 && (
                <Badge
                  variant="outline"
                  className={`text-[10px] px-1.5 py-0 h-4 ${
                    isSelected
                      ? 'border-primary-foreground/30 text-primary-foreground/80'
                      : 'border-muted-foreground/30'
                  }`}
                >
                  Default
                </Badge>
              )}
            </button>
          )
        })}
      </div>

      {/* Bulk actions */}
      {accounts.length > 1 && (
        <div className="flex gap-2 border-l border-border pl-4">
          <Button
            size="sm"
            variant="ghost"
            onClick={handleSelectAll}
            disabled={allSelected}
            className="text-xs h-8"
          >
            <CheckSquare className="w-3.5 h-3.5 mr-1.5" />
            All
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleDeselectAll}
            disabled={noneSelected}
            className="text-xs h-8"
          >
            <XSquare className="w-3.5 h-3.5 mr-1.5" />
            None
          </Button>
        </div>
      )}
    </div>
  )
}

export default MultiAccountSelector
