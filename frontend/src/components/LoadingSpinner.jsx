import React from 'react'
import { Loader2 } from 'lucide-react'

function LoadingSpinner({ size = 'md', message = 'Loading...' }) {
  const sizeClasses = {
    sm: 'w-5 h-5',
    md: 'w-8 h-8',
    lg: 'w-12 h-12',
  }

  return (
    <div className="flex flex-col items-center justify-center gap-4">
      <div className="relative">
        <div className={`${sizeClasses[size]} animate-spin`}>
          <Loader2 className={`${sizeClasses[size]} text-primary`} />
        </div>
        <div className={`absolute inset-0 ${sizeClasses[size]} animate-pulse rounded-full bg-primary/20 blur-md`} />
      </div>
      {message && (
        <p className="text-muted-foreground font-medium text-sm">{message}</p>
      )}
    </div>
  )
}

export default LoadingSpinner
