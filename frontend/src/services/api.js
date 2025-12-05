import axios from 'axios'

const API_BASE_URL = 'http://localhost:5000/api'

// Create axios instance with base configuration
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Helper function to check if error is retryable
const isRetryableError = (error) => {
  // Retry on network errors and 5xx errors
  return (
    error.code === 'ECONNABORTED' ||
    error.code === 'ETIMEDOUT' ||
    error.code === 'ERR_NETWORK' ||
    (error.response && [502, 503, 504].includes(error.response.status))
  )
}

// Response interceptor with retry logic and enhanced error handling
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config

    // Initialize retry count
    if (!config.__retryCount) {
      config.__retryCount = 0
    }

    // Retry logic for transient errors
    if (config.__retryCount < 3 && isRetryableError(error)) {
      config.__retryCount++
      const delay = Math.pow(2, config.__retryCount) * 1000
      console.log(`Retrying request (attempt ${config.__retryCount}) after ${delay}ms`)
      await new Promise((resolve) => setTimeout(resolve, delay))
      return apiClient(config)
    }

    // Detailed error messages based on status code
    let message = 'Unknown error occurred'
    let retryable = false

    if (error.response) {
      const status = error.response.status
      const data = error.response.data

      if (status === 429) {
        // Extract rate limit info if available
        const rateLimitInfo = data?.rate_limit || {}
        message = rateLimitInfo.message || 'Rate limit exceeded. Please wait and try again.'
        retryable = false  // Don't auto-retry rate limits
      } else if (status === 401) {
        message = 'Authentication failed. Please re-authenticate your account.'
      } else if (status === 400) {
        message = data?.error || 'Invalid request. Please check your input.'
      } else if (status === 500) {
        if (data?.error?.includes('FLOOD')) {
          message = 'Telegram rate limit hit. System will automatically slow down.'
        } else if (data?.error?.includes('database is locked')) {
          message = 'Database is busy. Please retry in a moment.'
          retryable = true
        } else {
          message = data?.error || 'Server error. Please contact support.'
        }
      } else if (status >= 500) {
        message = 'Server is temporarily unavailable. Please retry.'
        retryable = true
      }

      return Promise.reject({
        status,
        message,
        originalError: data?.error,
        retryable,
        errorType: data?.error_type,
        rateLimit: data?.rate_limit,
      })
    } else if (error.request) {
      message = 'Network error. Please check your connection.'
      retryable = true
      return Promise.reject({
        status: 0,
        message,
        retryable,
      })
    }

    return Promise.reject(error)
  }
)

// ============================================================================
// CACHING
// ============================================================================

// Simple in-memory cache
const cache = new Map()
const CACHE_TTL = 60000 // 1 minute

const getCacheKey = (endpoint, params) => {
  return `${endpoint}:${JSON.stringify(params || {})}`
}

export const getCachedRequest = async (endpoint, useCache = true, ttl = CACHE_TTL) => {
  const cacheKey = getCacheKey(endpoint)
  const cached = cache.get(cacheKey)

  if (useCache && cached && Date.now() - cached.timestamp < ttl) {
    return { success: true, data: cached.data, fromCache: true }
  }

  try {
    const response = await apiClient.get(endpoint)
    cache.set(cacheKey, {
      data: response.data,
      timestamp: Date.now(),
    })
    return { success: true, data: response.data, fromCache: false }
  } catch (error) {
    return { success: false, error }
  }
}

// Function to invalidate cache
export const invalidateCache = (endpoint = null) => {
  if (endpoint) {
    const cacheKey = getCacheKey(endpoint)
    cache.delete(cacheKey)
  } else {
    cache.clear()
  }
}

// ============================================================================
// VALIDATION UTILITIES
// ============================================================================

export const validatePhone = (phone) => {
  const phoneRegex = /^\+\d{7,15}$/
  if (!phone) {
    return { valid: false, error: 'Phone number is required' }
  }
  if (!phoneRegex.test(phone)) {
    return { valid: false, error: 'Phone must be in format +1234567890 (7-15 digits)' }
  }
  return { valid: true }
}

export const validateApiCredentials = (apiId, apiHash) => {
  if (!apiId || !apiHash) {
    return { valid: false, error: 'API ID and API Hash are required' }
  }
  if (!/^\d+$/.test(apiId)) {
    return { valid: false, error: 'API ID must be a number' }
  }
  if (apiHash.length < 32) {
    return { valid: false, error: 'API Hash appears invalid (too short)' }
  }
  return { valid: true }
}

export const validateCsvFile = (file) => {
  if (!file) {
    return { valid: false, error: 'Please select a CSV file' }
  }
  if (!file.name.endsWith('.csv')) {
    return { valid: false, error: 'File must be a CSV file' }
  }
  if (file.size > 10 * 1024 * 1024) {
    // 10MB
    return { valid: false, error: 'File too large (max 10MB)' }
  }
  return { valid: true }
}

// ============================================================================
// API FUNCTIONS
// ============================================================================

// Health & Status
export const checkHealth = async () => {
  try {
    const response = await apiClient.get('/health')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const getStatus = async () => {
  try {
    const response = await apiClient.get('/status')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Statistics (with caching)
// Supports both single and multi-account queries:
// - Single: getStats('88807942561')
// - Multiple: getStats('88807942561,12345678901')
export const getStats = async (phoneOrPhones = null, useCache = true) => {
  let endpoint = '/stats'

  if (phoneOrPhones) {
    // Check if it's a comma-separated list (multiple phones)
    if (phoneOrPhones.includes(',')) {
      endpoint = `/stats?phones=${encodeURIComponent(phoneOrPhones)}`
    } else {
      // Single phone (backward compatible)
      endpoint = `/stats?phone=${encodeURIComponent(phoneOrPhones)}`
    }
  }

  return getCachedRequest(endpoint, useCache)
}

// Configuration
export const getConfig = async () => {
  try {
    const response = await apiClient.get('/config')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const updateRateLimit = async (config) => {
  try {
    const response = await apiClient.post('/config/rate-limit', config)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Import Operations
export const importDevs = async (data) => {
  try {
    // Use longer timeout for import operation (10 minutes) - imports can take a while
    const response = await apiClient.post('/import/devs', data, {
      timeout: 600000, // 10 minutes - axios uses default Content-Type: application/json
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const importKols = async (data) => {
  try {
    // Use longer timeout for import operation (10 minutes) - imports can take a while
    const response = await apiClient.post('/import/kols', data, {
      timeout: 600000, // 10 minutes - axios uses default Content-Type: application/json
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Operations
export const scanReplies = async (params = {}, phone = null) => {
  try {
    const payload = { ...params }
    if (phone) {
      payload.phone = phone
    }
    // Use longer timeout for scan operation (10 minutes) - scans take 5-7 min per 100 dialogs
    const response = await apiClient.post('/scan-replies', payload, {
      timeout: 600000, // 10 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const organizeFolders = async (phone = null) => {
  try {
    const payload = phone ? { phone } : {}
    // Use longer timeout for organize operation (5 minutes)
    const response = await apiClient.post('/organize-folders', payload, {
      timeout: 300000, // 5 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

/**
 * Start a multi-account operation with WebSocket progress tracking
 * @param {string} operation - 'scan' | 'backup' | 'folders'
 * @param {string[]} phones - Array of phone numbers
 * @param {object} params - Operation-specific parameters
 * @returns {Promise<{success: boolean, data?: {operation_id: string}, error?: any}>}
 */
export const startMultiAccountOperation = async (operation, phones, params = {}) => {
  try {
    const response = await apiClient.post('/operations/start', {
      operation,
      phones,
      params
    }, {
      timeout: 600000, // 10 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Sessions
export const getSessions = async () => {
  try {
    const response = await apiClient.get('/sessions')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const selectSession = async (sessionName) => {
  try {
    const response = await apiClient.post('/sessions/select', { session: sessionName })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// File Operations
export const uploadCsv = async (file) => {
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await apiClient.post('/upload-csv', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const getUploads = async () => {
  try {
    const response = await apiClient.get('/uploads')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Logs
export const getLogs = async (filter = 'all', page = 1, limit = 50) => {
  try {
    const response = await apiClient.get('/logs', {
      params: { filter, page, limit },
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const getLogDetail = async (logId) => {
  try {
    const response = await apiClient.get(`/logs/${logId}`)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Contacts
export const getContacts = async (params = {}) => {
  try {
    const response = await apiClient.get('/contacts', { params })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Account Management (with caching)
export const getAccounts = async (useCache = true) => {
  return getCachedRequest('/accounts', useCache)
}

export const getActiveAccounts = async (useCache = true) => {
  return getCachedRequest('/accounts/active', useCache)
}

export const addAccount = async (accountData) => {
  try {
    const response = await apiClient.post('/accounts/add', accountData)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const validateAccount = async (phone, apiId, apiHash) => {
  try {
    const response = await apiClient.post('/accounts/validate', { phone, api_id: apiId, api_hash: apiHash })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const validateAccountsBatch = async (phones) => {
  try {
    const response = await apiClient.post('/accounts/validate-batch', { phones })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const deleteAccount = async (phone) => {
  try {
    const response = await apiClient.delete(`/accounts/${encodeURIComponent(phone)}`)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const updateAccountStatus = async (phone, status) => {
  try {
    const response = await apiClient.put(`/accounts/${encodeURIComponent(phone)}/status`, { status })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const updateAccountProxy = async (phone, proxy) => {
  try {
    const response = await apiClient.put(`/accounts/${encodeURIComponent(phone)}/proxy`, { proxy })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Multi-account imports
export const importDevsMulti = async (csvPath, accountPhones, dryRun = false) => {
  try {
    // Use longer timeout for multi-import operation (15 minutes)
    const response = await apiClient.post('/import/devs/multi', {
      csv_path: csvPath,
      account_phones: accountPhones,
      dry_run: dryRun
    }, {
      timeout: 900000, // 15 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const importKolsMulti = async (csvPath, accountPhones, dryRun = false) => {
  try {
    // Use longer timeout for multi-import operation (15 minutes)
    const response = await apiClient.post('/import/kols/multi', {
      csv_path: csvPath,
      account_phones: accountPhones,
      dry_run: dryRun
    }, {
      timeout: 900000, // 15 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Authentication
export const startAuth = async (phone) => {
  try {
    const response = await apiClient.post('/auth/start', { phone })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const submitCode = async (phone, code) => {
  try {
    const response = await apiClient.post('/auth/submit-code', { phone, code })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const submitPassword = async (phone, password) => {
  try {
    const response = await apiClient.post('/auth/submit-password', { phone, password })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Audit Log
export const getAuditLog = async (params = {}) => {
  try {
    const response = await apiClient.get('/audit', { params })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Authentication Flow
export const sendAuthCode = async (phone, apiId, apiHash, proxy = null) => {
  try {
    const payload = {
      phone,
      api_id: apiId,
      api_hash: apiHash
    }
    // Only include proxy if provided (to avoid sending empty string)
    if (proxy) {
      payload.proxy = proxy
    }
    const response = await apiClient.post('/auth/send-code', payload)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const verifyAuthCode = async (phone, code) => {
  try {
    const response = await apiClient.post('/auth/verify-code', { phone, code })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const verifyAuthPassword = async (phone, password) => {
  try {
    const response = await apiClient.post('/auth/verify-password', { phone, password })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Backup Contacts
export const backupContacts = async (phone = null) => {
  try {
    const payload = phone ? { phone } : {}
    // Use longer timeout for backup operation (5 minutes)
    const response = await apiClient.post('/backup-contacts', payload, {
      timeout: 300000, // 5 minutes
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

// Operation Management
export const getActiveOperations = async () => {
  try {
    const response = await apiClient.get('/operations/active')
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const getOperationsHistory = async (limit = 20) => {
  try {
    const response = await apiClient.get('/operations/history', {
      params: { limit }
    })
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export const getOperationDetails = async (operationId) => {
  try {
    const response = await apiClient.get(`/operations/${operationId}`)
    return { success: true, data: response.data }
  } catch (error) {
    return { success: false, error }
  }
}

export default apiClient
