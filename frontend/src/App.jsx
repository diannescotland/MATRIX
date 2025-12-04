import React, { useState, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import './App.css'

// Import Context
import { AccountProvider } from './context/AccountContext'
import { GlobalProgressProvider } from './context/GlobalProgressContext'

// Import Global Components
import GlobalProgressPanel from './components/GlobalProgressPanel'

// Import Pages
import Dashboard from './pages/Dashboard'
import Contacts from './pages/Contacts'
import Import from './pages/Import'
import Operations from './pages/Operations'
import Settings from './pages/Settings'
import Logs from './pages/Logs'
import Accounts from './pages/Accounts'
import AuditLog from './pages/AuditLog'

// Import Components
import Navbar from './components/Navbar'
import Sidebar from './components/Sidebar'

function App() {
  const [isConnected, setIsConnected] = useState(false)

  useEffect(() => {
    checkConnection()
    const interval = setInterval(checkConnection, 10000)
    return () => clearInterval(interval)
  }, [])

  const checkConnection = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/health')
      setIsConnected(response.ok)
    } catch (error) {
      setIsConnected(false)
    }
  }

  return (
    <AccountProvider>
      <GlobalProgressProvider>
        <Router>
          <div className="App flex h-screen bg-background">
            <Sidebar />
            <div className="flex-1 flex flex-col min-h-0">
              <Navbar isConnected={isConnected} />
              <main className="flex-1 overflow-y-auto overflow-x-hidden bg-background relative">
                <Routes>
                  <Route path="/" element={<Dashboard isConnected={isConnected} />} />
                  <Route path="/contacts" element={<Contacts isConnected={isConnected} />} />
                  <Route path="/import" element={<Import isConnected={isConnected} />} />
                  <Route path="/operations" element={<Operations isConnected={isConnected} />} />
                  <Route path="/accounts" element={<Accounts isConnected={isConnected} />} />
                  <Route path="/settings" element={<Settings isConnected={isConnected} />} />
                  <Route path="/logs" element={<Logs isConnected={isConnected} />} />
                  <Route path="/audit" element={<AuditLog isConnected={isConnected} />} />
                </Routes>
              </main>
            </div>
            {/* Global Progress Panel - Fixed overlay visible from all pages */}
            <GlobalProgressPanel />
          </div>
        </Router>
      </GlobalProgressProvider>
    </AccountProvider>
  )
}

export default App
