import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Users,
  Upload,
  Settings2,
  Smartphone,
  Cog,
  FileText,
  Menu,
  X,
  Zap
} from 'lucide-react'

function Sidebar() {
  const location = useLocation()
  const [isOpen, setIsOpen] = useState(false)

  const navItems = [
    { label: 'Dashboard', path: '/', icon: LayoutDashboard },
    { label: 'Contacts', path: '/contacts', icon: Users },
    { label: 'Import', path: '/import', icon: Upload },
    { label: 'Operations', path: '/operations', icon: Settings2 },
    { label: 'Accounts', path: '/accounts', icon: Smartphone },
    { label: 'Settings', path: '/settings', icon: Cog },
    { label: 'Logs', path: '/logs', icon: FileText },
  ]

  const isActive = (path) => location.pathname === path

  return (
    <>
      {/* Mobile menu button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="md:hidden fixed top-4 left-4 z-50 bg-primary text-primary-foreground p-2.5 rounded-xl shadow-elevated hover:bg-primary/90 transition-all duration-200 active:scale-95"
      >
        {isOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>

      {/* Sidebar */}
      <aside
        className={`${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        } md:translate-x-0 fixed md:relative w-64 h-screen glass-strong border-r border-border/50 transition-transform duration-300 z-40 flex flex-col`}
      >
        {/* Logo */}
        <div className="p-5 border-b border-border/50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl gradient-primary flex items-center justify-center shadow-glow glow-blue">
              <Zap className="h-5 w-5 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gradient">MATRIX</h2>
              <p className="text-xs text-muted-foreground">Contact Manager</p>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 px-3 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon
            const active = isActive(item.path)
            return (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setIsOpen(false)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group ${
                  active
                    ? 'bg-primary text-primary-foreground shadow-glow glow-blue'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
              >
                <Icon className={`h-5 w-5 transition-transform duration-200 ${active ? '' : 'group-hover:scale-110'}`} />
                <span className="font-medium">{item.label}</span>
                {active && (
                  <div className="ml-auto w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                )}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-border/50">
          <div className="px-4 py-3 rounded-xl bg-accent/50">
            <p className="text-xs font-medium text-muted-foreground">Version</p>
            <p className="text-sm font-semibold text-foreground">v1.0.0</p>
          </div>
        </div>
      </aside>

      {/* Mobile overlay */}
      {isOpen && (
        <div
          onClick={() => setIsOpen(false)}
          className="md:hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-30 transition-opacity duration-300"
        />
      )}
    </>
  )
}

export default Sidebar
