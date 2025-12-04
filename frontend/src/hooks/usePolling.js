import { useState, useEffect, useCallback } from 'react'

export const usePolling = (apiCall, interval = 2000, shouldPoll = true) => {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const poll = useCallback(async () => {
    try {
      setError(null)
      const response = await apiCall()
      if (response.success) {
        setData(response.data)
      } else {
        setError(response.error)
      }
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [apiCall])

  useEffect(() => {
    if (!shouldPoll) return

    // Initial call
    poll()

    // Set up polling interval
    const pollInterval = setInterval(poll, interval)

    return () => clearInterval(pollInterval)
  }, [poll, interval, shouldPoll])

  return { data, loading, error, refetch: poll }
}

export default usePolling
