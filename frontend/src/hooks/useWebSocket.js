/**
 * WebSocket Hook for MATRIX Real-Time Progress Updates
 *
 * This hook provides real-time communication with the MATRIX backend
 * for operation progress updates, logs, and completion notifications.
 *
 * Usage:
 *   const { isConnected, subscribe, operationProgress, logs } = useWebSocket();
 *
 *   // Subscribe to an operation
 *   subscribe('abc12345');
 *
 *   // Access progress for each account
 *   operationProgress.accounts['88807942561'].progress
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';

const SOCKET_URL = 'http://localhost:5000';

export function useWebSocket() {
  const [isConnected, setIsConnected] = useState(false);
  const [operationId, setOperationId] = useState(null);
  const [operationProgress, setOperationProgress] = useState(null);
  const [logs, setLogs] = useState([]);
  const [operationResult, setOperationResult] = useState(null);
  const [batchDelay, setBatchDelay] = useState(null);
  const socketRef = useRef(null);
  const batchDelayIntervalRef = useRef(null);

  // Initialize socket connection
  useEffect(() => {
    const socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });

    socketRef.current = socket;

    socket.on('connect', () => {
      console.log('WebSocket connected');
      setIsConnected(true);
    });

    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
    });

    socket.on('connected', (data) => {
      console.log('Server greeting:', data.message);
    });

    socket.on('error', (data) => {
      console.error('WebSocket error:', data.message);
    });

    // Handle initial operation state
    socket.on('operation_state', (data) => {
      console.log('Received operation state:', data);
      setOperationProgress(data);
      // Extract logs from accounts
      const allLogs = [];
      Object.values(data.accounts || {}).forEach((account) => {
        account.logs?.forEach((log) => {
          allLogs.push({ ...log, phone: account.phone });
        });
      });
      setLogs(allLogs);
    });

    // Handle progress updates
    socket.on('operation_progress', (data) => {
      console.log('Progress update:', data);
      setOperationProgress((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          accounts: {
            ...prev.accounts,
            [data.phone]: {
              ...prev.accounts[data.phone],
              progress: data.progress,
              total: data.total,
              status: data.status,
              message: data.message,
              error: data.error,
              stats: data.stats || prev.accounts[data.phone]?.stats,
            },
          },
        };
      });
    });

    // Handle batch delay countdown events
    socket.on('batch_delay_start', (data) => {
      console.log('Batch delay started:', data);

      // Clear any existing interval
      if (batchDelayIntervalRef.current) {
        clearInterval(batchDelayIntervalRef.current);
      }

      const startedAt = Date.now();
      setBatchDelay({
        ...data,
        startedAt,
        progress: 0,
      });

      // Animate progress bar from 0 to 100 over delay_seconds
      batchDelayIntervalRef.current = setInterval(() => {
        setBatchDelay((prev) => {
          if (!prev) {
            clearInterval(batchDelayIntervalRef.current);
            return null;
          }
          const elapsed = (Date.now() - prev.startedAt) / 1000;
          const progress = Math.min(100, (elapsed / prev.delay_seconds) * 100);
          if (progress >= 100) {
            clearInterval(batchDelayIntervalRef.current);
            // Keep showing 100% briefly before clearing
            setTimeout(() => setBatchDelay(null), 500);
            return { ...prev, progress: 100 };
          }
          return { ...prev, progress };
        });
      }, 100);
    });

    // Handle log messages
    socket.on('operation_log', (data) => {
      console.log('Log message:', data);
      setLogs((prev) => [...prev, { ...data.log, phone: data.phone }]);
    });

    // Handle operation completion
    socket.on('operation_complete', (data) => {
      console.log('Operation complete:', data);
      setOperationResult(data);
      setOperationProgress((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          status: data.status,
          results: data.results,
          error: data.error,
        };
      });
    });

    // Cleanup on unmount
    return () => {
      if (batchDelayIntervalRef.current) {
        clearInterval(batchDelayIntervalRef.current);
      }
      socket.disconnect();
    };
  }, []);

  // Subscribe to an operation
  const subscribe = useCallback((opId) => {
    if (socketRef.current && opId) {
      console.log('Subscribing to operation:', opId);
      setOperationId(opId);
      setLogs([]);
      setOperationResult(null);
      socketRef.current.emit('subscribe_operation', { operation_id: opId });
    }
  }, []);

  // Unsubscribe from current operation
  const unsubscribe = useCallback(() => {
    if (socketRef.current && operationId) {
      console.log('Unsubscribing from operation:', operationId);
      socketRef.current.emit('unsubscribe_operation', { operation_id: operationId });
      setOperationId(null);
      setOperationProgress(null);
      setLogs([]);
      setOperationResult(null);
    }
  }, [operationId]);

  // Clear current state
  const clearProgress = useCallback(() => {
    setOperationId(null);
    setOperationProgress(null);
    setLogs([]);
    setOperationResult(null);
    setBatchDelay(null);
    if (batchDelayIntervalRef.current) {
      clearInterval(batchDelayIntervalRef.current);
    }
  }, []);

  // Get overall progress percentage
  const getOverallProgress = useCallback(() => {
    if (!operationProgress?.accounts) return 0;

    const accounts = Object.values(operationProgress.accounts);
    if (accounts.length === 0) return 0;

    const totalProgress = accounts.reduce((sum, acc) => sum + (acc.progress || 0), 0);
    const totalMax = accounts.reduce((sum, acc) => sum + (acc.total || 100), 0);

    return totalMax > 0 ? Math.round((totalProgress / totalMax) * 100) : 0;
  }, [operationProgress]);

  // Get status counts
  const getStatusCounts = useCallback(() => {
    if (!operationProgress?.accounts) {
      return { pending: 0, running: 0, completed: 0, error: 0 };
    }

    const accounts = Object.values(operationProgress.accounts);
    return {
      pending: accounts.filter((a) => a.status === 'pending').length,
      running: accounts.filter((a) => a.status === 'running').length,
      completed: accounts.filter((a) => a.status === 'completed').length,
      error: accounts.filter((a) => a.status === 'error').length,
    };
  }, [operationProgress]);

  // Get combined stats across all accounts
  const getCombinedStats = useCallback(() => {
    if (!operationProgress?.accounts) {
      return {
        added: 0,
        skipped: 0,
        failed: 0,
        successRate: 0,
        speed: 0,
        etaSeconds: 0,
        batchNumber: 0,
        totalBatchesEstimate: 0,
        totalProgress: 0,
        totalMax: 0,
      };
    }

    const accounts = Object.values(operationProgress.accounts);
    let totalAdded = 0;
    let totalSkipped = 0;
    let totalFailed = 0;
    let totalSpeed = 0;
    let maxEta = 0;
    let totalProgress = 0;
    let totalMax = 0;
    let accountsWithStats = 0;

    accounts.forEach((acc) => {
      totalProgress += acc.progress || 0;
      totalMax += acc.total || 0;

      if (acc.stats) {
        totalAdded += acc.stats.added || 0;
        totalSkipped += acc.stats.skipped || 0;
        totalFailed += acc.stats.failed || 0;
        totalSpeed += acc.stats.speed || 0;
        maxEta = Math.max(maxEta, acc.stats.eta_seconds || 0);
        accountsWithStats++;
      }
    });

    const totalProcessed = totalAdded + totalSkipped + totalFailed;
    const successRate = totalProcessed > 0 ? (totalAdded / totalProcessed) * 100 : 0;
    const avgSpeed = accountsWithStats > 0 ? totalSpeed / accountsWithStats : 0;

    // Get batch info from batchDelay if available
    const batchNumber = batchDelay?.batch_number || 0;
    const totalBatchesEstimate = batchDelay?.total_batches_estimate || 0;

    return {
      added: totalAdded,
      skipped: totalSkipped,
      failed: totalFailed,
      successRate: Math.round(successRate * 10) / 10,
      speed: Math.round(avgSpeed * 10) / 10,
      etaSeconds: maxEta,
      batchNumber,
      totalBatchesEstimate,
      totalProgress,
      totalMax,
    };
  }, [operationProgress, batchDelay]);

  return {
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
  };
}

export default useWebSocket;
