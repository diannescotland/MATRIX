import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { getActiveAccounts } from '../services/api'

export const AccountContext = createContext(null)

export function AccountProvider({ children }) {
  const [accounts, setAccounts] = useState([])
  const [selectedAccounts, setSelectedAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Fetch accounts on mount
  const fetchAccounts = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const result = await getActiveAccounts()

      if (result.success && result.data.accounts) {
        const fetchedAccounts = result.data.accounts
        setAccounts(fetchedAccounts)

        // Auto-select default account if nothing is selected
        if (selectedAccounts.length === 0) {
          const defaultAccount = fetchedAccounts.find(acc => acc.is_default === 1)
          if (defaultAccount) {
            setSelectedAccounts([defaultAccount])
          } else if (fetchedAccounts.length > 0) {
            setSelectedAccounts([fetchedAccounts[0]])
          }
        } else {
          // Refresh selected accounts with latest data
          const updatedSelection = selectedAccounts
            .map(selected => fetchedAccounts.find(acc => acc.phone === selected.phone))
            .filter(Boolean)
          setSelectedAccounts(updatedSelection)
        }
      } else {
        setError(result.error?.message || 'Failed to fetch accounts')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAccounts()
  }, [fetchAccounts])

  // Toggle account selection
  const toggleAccount = useCallback((account) => {
    setSelectedAccounts(prev => {
      const isSelected = prev.some(acc => acc.phone === account.phone)
      if (isSelected) {
        return prev.filter(acc => acc.phone !== account.phone)
      } else {
        return [...prev, account]
      }
    })
  }, [])

  // Select all accounts
  const selectAll = useCallback(() => {
    setSelectedAccounts(accounts)
  }, [accounts])

  // Deselect all accounts
  const deselectAll = useCallback(() => {
    setSelectedAccounts([])
  }, [])

  // Check if account is selected
  const isSelected = useCallback((account) => {
    return selectedAccounts.some(acc => acc.phone === account.phone)
  }, [selectedAccounts])

  // Get comma-separated phone list for API calls
  const getPhoneList = useCallback(() => {
    return selectedAccounts.map(acc => acc.phone).join(',')
  }, [selectedAccounts])

  // Get phone array for multi-account operations
  const getPhoneArray = useCallback(() => {
    return selectedAccounts.map(acc => acc.phone)
  }, [selectedAccounts])

  const value = {
    // State
    accounts,
    selectedAccounts,
    loading,
    error,

    // Actions
    setSelectedAccounts,
    toggleAccount,
    selectAll,
    deselectAll,
    isSelected,
    refetchAccounts: fetchAccounts,

    // Helpers
    getPhoneList,
    getPhoneArray,

    // Computed
    hasAccounts: accounts.length > 0,
    hasSelection: selectedAccounts.length > 0,
    allSelected: accounts.length > 0 && selectedAccounts.length === accounts.length,
    selectionCount: selectedAccounts.length,
  }

  return (
    <AccountContext.Provider value={value}>
      {children}
    </AccountContext.Provider>
  )
}

export function useAccounts() {
  const context = useContext(AccountContext)
  if (!context) {
    throw new Error('useAccounts must be used within an AccountProvider')
  }
  return context
}

export default AccountContext
