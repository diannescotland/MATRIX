import React from 'react'
import { useLocation } from 'react-router-dom'
import { useAccounts } from '../context/AccountContext'
import { Badge } from '@/components/ui/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import MultiAccountSelector from './MultiAccountSelector'
import { Smartphone, Wifi, WifiOff, ChevronDown } from 'lucide-react'

function Navbar({ isConnected }) {
  const location = useLocation()
  const { accounts, selectedAccounts, setSelectedAccounts, selectionCount, hasSelection } = useAccounts()

  const getPageTitle = () => {
    const pathMap = {
      '/': 'Dashboard',
      '/contacts': 'Contacts',
      '/import': 'Import Contacts',
      '/operations': 'Operations',
      '/settings': 'Settings',
      '/logs': 'Operation Logs',
      '/accounts': 'Accounts',
      '/audit': 'Audit Log',
    }
    return pathMap[location.pathname] || 'MATRIX Dashboard'
  }

  // Get display text for selected accounts
  const getAccountsDisplay = () => {
    if (!hasSelection) return 'No accounts'
    if (selectionCount === 1) {
      return selectedAccounts[0].name || selectedAccounts[0].phone
    }
    return `${selectionCount} accounts`
  }

  return (
    <nav className="glass-strong border-b border-border/50 px-6 py-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{getPageTitle()}</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Telegram Contact Management
          </p>
        </div>

        <div className="flex items-center gap-4">
          {/* Selected Accounts Indicator - Popover Dropdown */}
          <Popover>
            <PopoverTrigger asChild>
              <button className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-accent/50 hover:bg-accent transition-colors group">
                <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
                  <Smartphone className="h-4 w-4 text-primary" />
                </div>
                <div className="flex flex-col items-start">
                  <span className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                    {getAccountsDisplay()}
                  </span>
                  {hasSelection && (
                    <span className="text-xs text-muted-foreground">
                      {selectionCount === 1 ? 'Active account' : 'Selected'}
                    </span>
                  )}
                </div>
                <ChevronDown className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-4" align="end">
              <div className="space-y-3">
                <h4 className="font-medium text-sm text-foreground">Select Accounts</h4>
                <MultiAccountSelector
                  accounts={accounts}
                  selectedAccounts={selectedAccounts}
                  onSelectionChange={setSelectedAccounts}
                />
              </div>
            </PopoverContent>
          </Popover>

          {/* Connection Status */}
          <div className={`flex items-center gap-2 px-4 py-2.5 rounded-xl ${
            isConnected
              ? 'bg-green-500/10 border border-green-500/20'
              : 'bg-red-500/10 border border-red-500/20'
          }`}>
            <div className="relative">
              {isConnected ? (
                <Wifi className="h-4 w-4 text-green-500" />
              ) : (
                <WifiOff className="h-4 w-4 text-red-500" />
              )}
              <div className={`absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full ${
                isConnected ? 'bg-green-500' : 'bg-red-500'
              } ${isConnected ? 'animate-pulse' : ''}`} />
            </div>
            <span className={`text-sm font-medium ${
              isConnected ? 'text-green-500' : 'text-red-500'
            }`}>
              {isConnected ? 'Connected' : 'Offline'}
            </span>
          </div>
        </div>
      </div>
    </nav>
  )
}

export default Navbar
