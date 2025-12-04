# MATRIX Frontend Dashboard

A modern, interactive web dashboard for the MATRIX Telegram Contact Management System. Built with React, Tailwind CSS, and Vite.

## Features

✨ **Real-time Statistics** - View contact counts by type and status at a glance
📤 **CSV Import** - Upload and import developer and KOL contacts with dry-run preview
🔍 **Contact Management** - Search, filter, and manage all your contacts
⚙️ **Operations** - Run scans and folder organization with real-time progress tracking
🔧 **Settings** - Configure rate-limit parameters with presets (Conservative, Balanced, Aggressive)
📋 **Logs** - View operation history and detailed logs

## Quick Start

### Option 1: Unified Launcher (Recommended)

```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
python run_all.py
```

This will:
- ✅ Check all dependencies
- ✅ Start the Flask API server (http://localhost:5000)
- ✅ Install frontend dependencies (if needed)
- ✅ Start the React frontend (http://localhost:3000)
- ✅ Automatically open the dashboard in your browser

### Option 2: Manual Start

**Terminal 1 - Start Flask API Server:**
```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
python api_server.py
```

**Terminal 2 - Start React Frontend:**
```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX\frontend"
npm install  # (first time only)
npm run dev
```

Then open http://localhost:3000 in your browser.

### Option 3: Keep Using CLI

The original CLI still works:
```bash
python matrix.py
```

## Project Structure

```
frontend/
├── src/
│   ├── components/        # Reusable React components
│   │   ├── Navbar.jsx
│   │   ├── Sidebar.jsx
│   │   └── LoadingSpinner.jsx
│   ├── pages/             # Page components (one per route)
│   │   ├── Dashboard.jsx
│   │   ├── Contacts.jsx
│   │   ├── Import.jsx
│   │   ├── Operations.jsx
│   │   ├── Settings.jsx
│   │   └── Logs.jsx
│   ├── services/          # API client
│   │   └── api.js
│   ├── hooks/             # Custom React hooks
│   │   ├── useApi.js
│   │   └── usePolling.js
│   ├── App.jsx            # Main app component with routing
│   └── index.css          # Global styles & Tailwind
└── package.json           # npm dependencies
```

## Available Scripts

**Start development server:**
```bash
npm run dev
```

**Build for production:**
```bash
npm run build
```

**Preview production build:**
```bash
npm run preview
```

## Pages

- **Dashboard** (/) - Statistics overview & quick actions
- **Contacts** (/contacts) - View and manage contacts
- **Import** (/import) - Upload CSV files
- **Operations** (/operations) - Run scans and organize folders
- **Settings** (/settings) - Configure rate limits
- **Logs** (/logs) - View operation history

## Troubleshooting

### API Not Responding
Make sure the Flask API is running:
```bash
python api_server.py
```

### Node Modules Missing
```bash
npm install
```

### Port Already in Use
```bash
# Kill process on port 3000 and retry
netstat -ano | findstr :3000
taskkill /PID <PID> /F
```

## Technologies

- **React 18** - UI framework
- **React Router** - Client-side routing
- **Tailwind CSS** - Styling
- **Axios** - HTTP client
- **Vite** - Build tool

## License

MATRIX Dashboard © 2025
