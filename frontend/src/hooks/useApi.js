import { useState, useCallback } from 'react'

export const useApi = () => {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const request = useCallback(async (apiCall) => {
    setLoading(true)
    setError(null)
    try {
      const response = await apiCall()
      setLoading(false)
      return response
    } catch (err) {
      setError(err)
      setLoading(false)
      throw err
    }
  }, [])

  return { loading, error, request }
}

export default useApi
