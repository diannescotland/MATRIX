import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import {
  saveActiveOperation,
  getSavedOperation,
  clearSavedOperation,
  validateSavedOperation
} from '../services/operationPersistence'

export const GlobalProgressContext = createContext(null)

export function GlobalProgressProvider({ children }) {
  const {
    isConnected,
    operationId,
    operationProgress,
    logs,
    operationResult,
    batchDelay,
    subscribe,
    unsubscribe,
    clearProgress,
    getOverallProgress,
    getStatusCounts,
    getCombinedStats,
  } = useWebSocket()

  const [panelVisible, setPanelVisible] = useState(false)
  const [panelMinimized, setPanelMinimized] = useState(false)
  const [operationType, setOperationType] = useState(null) // 'import_devs', 'import_kols', 'scan', 'backup', 'organize'
  const [reconnecting, setReconnecting] = useState(false)
  const hasCheckedSavedOperation = useRef(false)

  // Auto-reconnect to saved operation when WebSocket connects
  useEffect(() => {
    if (!isConnected || hasCheckedSavedOperation.current) return

    const checkSavedOperation = async () => {
      hasCheckedSavedOperation.current = true
      setReconnecting(true)

      try {
        const result = await validateSavedOperation()

        if (result.isActive && result.operationId) {
          // Reconnect to the running operation
          console.log('🔄 Reconnecting to saved operation:', result.operationId)
          subscribe(result.operationId)
          setOperationType(result.type)
          setPanelVisible(true)
          setPanelMinimized(false)
        }
      } catch (e) {
        console.warn('Failed to reconnect to saved operation:', e)
      } finally {
        setReconnecting(false)
      }
    }

    checkSavedOperation()
  }, [isConnected, subscribe])

  // Clear localStorage when operation completes
  useEffect(() => {
    if (operationResult && (operationResult.status === 'completed' || operationResult.status === 'error')) {
      clearSavedOperation()
    }
  }, [operationResult])

  // Start tracking an operation
  const startOperation = useCallback((opId, type = 'import', phones = []) => {
    // Save to localStorage for reconnection after refresh
    saveActiveOperation(opId, type, phones)

    subscribe(opId)
    setOperationType(type)
    setPanelVisible(true)
    setPanelMinimized(false)
  }, [subscribe])

  // Stop tracking and hide panel
  const stopOperation = useCallback(() => {
    // Clear localStorage when user explicitly stops
    clearSavedOperation()

    unsubscribe()
    setOperationType(null)
    setPanelVisible(false)
    setPanelMinimized(false)
  }, [unsubscribe])

  // Hide panel but keep tracking
  const hidePanel = useCallback(() => {
    setPanelVisible(false)
  }, [])

  // Show panel
  const showPanel = useCallback(() => {
    setPanelVisible(true)
    setPanelMinimized(false)
  }, [])

  // Toggle minimize
  const toggleMinimize = useCallback(() => {
    setPanelMinimized(prev => !prev)
  }, [])

  // Check if operation is active
  const isOperationActive = operationId && operationProgress &&
    operationProgress.status !== 'completed' &&
    operationProgress.status !== 'error'

  // Get operation display name
  const getOperationDisplayName = useCallback(() => {
    switch (operationType) {
      case 'import_devs': return 'Importing Developers'
      case 'import_kols': return 'Importing KOLs'
      case 'scan': return 'Scanning Replies'
      case 'backup': return 'Backing Up Contacts'
      case 'organize': return 'Organizing Folders'
      default: return 'Operation'
    }
  }, [operationType])

  // Format ETA
  const formatEta = useCallback((seconds) => {
    if (!seconds || seconds <= 0) return '--'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    if (mins > 0) {
      return `${mins}m ${secs}s`
    }
    return `${secs}s`
  }, [])

  // Format speed
  const formatSpeed = useCallback((speed) => {
    if (!speed || speed <= 0) return '--'
    return `${speed}/min`
  }, [])

  const value = {
    // WebSocket state
    isConnected,
    operationId,
    operationProgress,
    logs,
    operationResult,
    batchDelay,

    // Panel state
    panelVisible,
    panelMinimized,
    operationType,
    isOperationActive,
    reconnecting,

    // Actions
    startOperation,
    stopOperation,
    hidePanel,
    showPanel,
    toggleMinimize,
    clearProgress,

    // Computed helpers
    getOverallProgress,
    getStatusCounts,
    getCombinedStats,
    getOperationDisplayName,
    formatEta,
    formatSpeed,
  }

  return (
    <GlobalProgressContext.Provider value={value}>
      {children}
    </GlobalProgressContext.Provider>
  )
}

export function useGlobalProgress() {
  const context = useContext(GlobalProgressContext)
  if (!context) {
    throw new Error('useGlobalProgress must be used within a GlobalProgressProvider')
  }
  return context
}

export default GlobalProgressContext
