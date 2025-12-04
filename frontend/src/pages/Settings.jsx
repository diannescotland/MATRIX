import React, { useState, useEffect } from 'react'
import { getConfig, updateRateLimit } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertCircle,
  Info,
  Save,
  RotateCcw,
  Shield,
  Zap,
  Gauge,
  Timer,
  Package,
  Clock,
  CheckCircle
} from 'lucide-react'

function Settings({ isConnected }) {
  const [config, setConfig] = useState({
    batch_size_min: 3,
    batch_size_max: 7,
    delay_min: 2,
    delay_max: 6,
    pause_min: 45,
    pause_max: 90,
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    if (isConnected) {
      fetchConfig()
    } else {
      setLoading(false)
    }
  }, [isConnected])

  const fetchConfig = async () => {
    const result = await getConfig()
    if (result.success && result.data) {
      // Map API response fields to our state format
      const rateLimit = result.data.rate_limit || result.data
      setConfig({
        batch_size_min: rateLimit.batch_size_min || 3,
        batch_size_max: rateLimit.batch_size_max || 7,
        delay_min: rateLimit.delay_per_contact_min || rateLimit.delay_min || 2,
        delay_max: rateLimit.delay_per_contact_max || rateLimit.delay_max || 6,
        pause_min: rateLimit.batch_pause_min || rateLimit.pause_min || 45,
        pause_max: rateLimit.batch_pause_max || rateLimit.pause_max || 90,
      })
    }
    setLoading(false)
  }

  const handleSave = async () => {
    setSaving(true)
    const result = await updateRateLimit(config)
    setSaving(false)
    if (result.success) {
      setMessage({ type: 'success', text: 'Settings saved successfully!' })
      setTimeout(() => setMessage(null), 3000)
    } else {
      setMessage({ type: 'error', text: 'Failed to save settings' })
    }
  }

  const applyPreset = (preset) => {
    const presets = {
      conservative: {
        batch_size_min: 1,
        batch_size_max: 3,
        delay_min: 5,
        delay_max: 10,
        pause_min: 60,
        pause_max: 120,
      },
      balanced: {
        batch_size_min: 3,
        batch_size_max: 7,
        delay_min: 2,
        delay_max: 6,
        pause_min: 45,
        pause_max: 90,
      },
      aggressive: {
        batch_size_min: 5,
        batch_size_max: 10,
        delay_min: 1,
        delay_max: 3,
        pause_min: 30,
        pause_max: 60,
      },
    }
    setConfig(presets[preset])
  }

  if (!isConnected) {
    return (
      <div className="p-6">
        <Alert variant="destructive" className="border-red-500/30 bg-red-500/5">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            API connection required. Make sure the Flask server is running.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[60vh]">
        <LoadingSpinner message="Loading settings..." />
      </div>
    )
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6 animate-fade-in">
      {message && (
        <Alert
          variant={message.type === 'success' ? 'default' : 'destructive'}
          className={message.type === 'success'
            ? 'border-green-500/30 bg-green-500/5'
            : 'border-red-500/30 bg-red-500/5'
          }
        >
          {message.type === 'success' ? (
            <CheckCircle className="h-4 w-4 text-green-500" />
          ) : (
            <AlertCircle className="h-4 w-4" />
          )}
          <AlertDescription className={message.type === 'success' ? 'text-green-500' : ''}>
            {message.text}
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="h-5 w-5 text-primary" />
            Rate-Limit Configuration
          </CardTitle>
          <CardDescription>
            Configure rate limiting to avoid Telegram restrictions and account bans
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">
          {/* Presets */}
          <div>
            <Label className="text-base font-semibold mb-4 block flex items-center gap-2">
              <Zap className="h-4 w-4 text-primary" />
              Quick Presets
            </Label>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <button
                onClick={() => applyPreset('conservative')}
                className="p-4 rounded-xl border-2 border-border bg-accent/30 hover:border-green-500/50 hover:bg-green-500/5 transition-all text-left group"
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-10 h-10 rounded-lg bg-green-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                    <Shield className="h-5 w-5 text-green-500" />
                  </div>
                  <div>
                    <p className="font-semibold text-foreground">Conservative</p>
                    <p className="text-xs text-muted-foreground">Safest option</p>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">
                  Slower but minimal rate limit risk. Recommended for new accounts.
                </p>
              </button>

              <button
                onClick={() => applyPreset('balanced')}
                className="p-4 rounded-xl border-2 border-primary/50 bg-primary/5 hover:border-primary hover:bg-primary/10 transition-all text-left group"
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-10 h-10 rounded-lg bg-primary/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                    <Gauge className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <p className="font-semibold text-foreground">Balanced</p>
                    <p className="text-xs text-primary">Recommended</p>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">
                  Good balance of speed and safety. Works for most accounts.
                </p>
              </button>

              <button
                onClick={() => applyPreset('aggressive')}
                className="p-4 rounded-xl border-2 border-border bg-accent/30 hover:border-orange-500/50 hover:bg-orange-500/5 transition-all text-left group"
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-10 h-10 rounded-lg bg-orange-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                    <Zap className="h-5 w-5 text-orange-500" />
                  </div>
                  <div>
                    <p className="font-semibold text-foreground">Aggressive</p>
                    <p className="text-xs text-orange-500">Higher risk</p>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">
                  Faster but higher rate limit risk. Use with caution.
                </p>
              </button>
            </div>
          </div>

          {/* Batch Size */}
          <div className="p-5 rounded-xl bg-accent/30 border border-border/50 space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                <Package className="h-5 w-5 text-blue-400" />
              </div>
              <div>
                <h4 className="font-semibold text-foreground">Batch Size</h4>
                <p className="text-sm text-muted-foreground">Number of contacts to process in each batch</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Minimum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.batch_size_min}
                  </span>
                </div>
                <Slider
                  value={[config.batch_size_min]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, batch_size_min: value })
                  }
                  min={1}
                  max={10}
                  step={1}
                  className="cursor-pointer"
                />
              </div>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Maximum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.batch_size_max}
                  </span>
                </div>
                <Slider
                  value={[config.batch_size_max]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, batch_size_max: value })
                  }
                  min={1}
                  max={10}
                  step={1}
                  className="cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Per-Contact Delay */}
          <div className="p-5 rounded-xl bg-accent/30 border border-border/50 space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center">
                <Timer className="h-5 w-5 text-purple-400" />
              </div>
              <div>
                <h4 className="font-semibold text-foreground">Per-Contact Delay</h4>
                <p className="text-sm text-muted-foreground">Wait time between processing each contact (seconds)</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Minimum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.delay_min}s
                  </span>
                </div>
                <Slider
                  value={[config.delay_min]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, delay_min: value })
                  }
                  min={1}
                  max={30}
                  step={1}
                  className="cursor-pointer"
                />
              </div>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Maximum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.delay_max}s
                  </span>
                </div>
                <Slider
                  value={[config.delay_max]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, delay_max: value })
                  }
                  min={1}
                  max={30}
                  step={1}
                  className="cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Batch Pause */}
          <div className="p-5 rounded-xl bg-accent/30 border border-border/50 space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-orange-500/20 flex items-center justify-center">
                <Clock className="h-5 w-5 text-orange-400" />
              </div>
              <div>
                <h4 className="font-semibold text-foreground">Batch Pause</h4>
                <p className="text-sm text-muted-foreground">Wait time between batches (seconds)</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Minimum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.pause_min}s
                  </span>
                </div>
                <Slider
                  value={[config.pause_min]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, pause_min: value })
                  }
                  min={30}
                  max={300}
                  step={5}
                  className="cursor-pointer"
                />
              </div>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-muted-foreground">Maximum</Label>
                  <span className="text-lg font-bold text-foreground bg-accent px-3 py-1 rounded-lg">
                    {config.pause_max}s
                  </span>
                </div>
                <Slider
                  value={[config.pause_max]}
                  onValueChange={([value]) =>
                    setConfig({ ...config, pause_max: value })
                  }
                  min={30}
                  max={300}
                  step={5}
                  className="cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Info Box */}
          <Alert className="border-primary/30 bg-primary/5">
            <Info className="h-4 w-4 text-primary" />
            <AlertDescription className="text-muted-foreground">
              <span className="font-semibold text-foreground">Why These Settings Matter:</span>{' '}
              These settings help prevent Telegram from rate-limiting or banning your account.
              Lower values are safer but slower. Higher values are faster but riskier.
              Start with Balanced and adjust based on your results.
            </AlertDescription>
          </Alert>

          {/* Action Buttons */}
          <div className="flex gap-4 pt-2">
            <Button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 h-12 text-base hover-scale"
              size="lg"
            >
              <Save className={`h-5 w-5 mr-2 ${saving ? 'animate-spin' : ''}`} />
              {saving ? 'Saving...' : 'Save Settings'}
            </Button>
            <Button
              onClick={() => fetchConfig()}
              variant="outline"
              className="h-12 px-6 hover-scale"
              size="lg"
            >
              <RotateCcw className="h-5 w-5 mr-2" />
              Reset
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export default Settings
