/**
 * Operation Persistence Service
 *
 * Handles localStorage persistence for active operations,
 * allowing users to resume viewing progress after page refresh.
 */

const STORAGE_KEY = 'matrix_active_operation';

/**
 * Save active operation to localStorage
 * @param {string} operationId - The operation ID
 * @param {string} type - Operation type (import_devs, import_kols, scan, backup, folders)
 * @param {string[]} phones - Array of phone numbers involved
 */
export function saveActiveOperation(operationId, type, phones) {
  try {
    const data = {
      operationId,
      type,
      phones,
      savedAt: new Date().toISOString()
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (e) {
    console.warn('Failed to save operation to localStorage:', e);
  }
}

/**
 * Get saved operation from localStorage
 * @returns {Object|null} Saved operation data or null
 */
export function getSavedOperation() {
  try {
    const data = localStorage.getItem(STORAGE_KEY);
    if (!data) return null;
    return JSON.parse(data);
  } catch (e) {
    console.warn('Failed to read operation from localStorage:', e);
    return null;
  }
}

/**
 * Clear saved operation from localStorage
 */
export function clearSavedOperation() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (e) {
    console.warn('Failed to clear operation from localStorage:', e);
  }
}

/**
 * Validate if saved operation is still active on server
 * @param {string} baseUrl - API base URL
 * @returns {Promise<{isActive: boolean, operation: Object|null}>}
 */
export async function validateSavedOperation(baseUrl = 'http://localhost:5000') {
  const saved = getSavedOperation();
  if (!saved) {
    return { isActive: false, operation: null };
  }

  try {
    // Check if operation exists and is still running
    const response = await fetch(`${baseUrl}/api/operations/${saved.operationId}`);
    if (!response.ok) {
      // Operation not found, clear localStorage
      clearSavedOperation();
      return { isActive: false, operation: null };
    }

    const data = await response.json();
    const operation = data.operation || data;

    // Check if operation is still active
    const isActive = operation.status === 'pending' || operation.status === 'running';

    if (!isActive) {
      // Operation completed/failed, clear localStorage
      clearSavedOperation();
    }

    return {
      isActive,
      operation,
      operationId: saved.operationId,
      type: saved.type,
      phones: saved.phones
    };
  } catch (e) {
    console.warn('Failed to validate operation:', e);
    // Don't clear on network error - might be temporary
    return { isActive: false, operation: null, error: e.message };
  }
}

/**
 * Check if there's a saved operation that might need reconnection
 * @returns {boolean}
 */
export function hasSavedOperation() {
  return getSavedOperation() !== null;
}
