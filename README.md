# MATRIX - Multi-Account Telegram Contact Manager

Enterprise-grade system for managing Telegram outreach campaigns across multiple accounts with automated status tracking and organization.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Telethon](https://img.shields.io/badge/Telethon-1.33.0-blue.svg)](https://github.com/LonamiWebs/Telethon)
[![React](https://img.shields.io/badge/React-19.2.0-61dafb.svg)](https://reactjs.org/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

---

## Overview

MATRIX automates Telegram contact management for crypto/blockchain outreach teams. Import contacts from CSV, track replies, update status automatically, and organize into folders—all across multiple Telegram accounts in parallel.

**Key Features:**
- 🔵 **Multi-Account Import** - Distribute contacts across accounts with round-robin allocation
- 💬 **Reply Detection** - Scan dialogs and detect who responded
- ⚡ **Auto-Status Updates** - Change emoji from blue (🔵) to yellow (🟡) when contacts reply
- 📁 **Folder Organization** - Auto-create and organize 4 Telegram folders
- 📊 **CSV Export** - Export no-reply contacts for follow-up campaigns
- 🌐 **Web Interface** - React frontend for non-technical users
- 🔐 **Session Persistence** - Encrypted session storage, no re-auth needed
- 📝 **Audit Trail** - Complete operation logging for compliance

---

## Architecture

```
┌─────────────────┐      HTTP/JSON      ┌──────────────────┐
│  React Frontend │ ◄─────────────────► │  Flask REST API  │
│  (localhost:5173)│                     │ (localhost:5000) │
└─────────────────┘                     └──────────────────┘
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │  Contact Manager │
                                        │   (Telethon)     │
                                        └──────────────────┘
                                                 │
                                    ┌────────────┼────────────┐
                                    ▼            ▼            ▼
                              ┌─────────┐  ┌─────────┐  ┌─────────┐
                              │ SQLite  │  │ Sessions│  │Telegram │
                              │accounts │  │ (files) │  │   API   │
                              └─────────┘  └─────────┘  └─────────┘
```

**Components:**
- **Backend** - Python (Telethon, Flask, SQLite)
- **Frontend** - React + Vite + Tailwind CSS
- **Data Layer** - SQLite (accounts), file-based sessions, CSV imports/exports

---

## Quick Start

### 1. Prerequisites

```bash
# Python 3.8+
python --version

# Node.js 16+ (for frontend)
node --version
```

### 2. Installation

```bash
# Clone or download the project
cd MATRIX

# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend
npm install
cd ..
```

### 3. Initialize System

```bash
# Run setup script (creates database, directories, verifies dependencies)
python backend/setup.py
```

### 4. Configure API Credentials

Get your Telegram API credentials from https://my.telegram.org/apps

First run will prompt for:
- **API ID** (numeric)
- **API Hash** (alphanumeric string)
- **Phone number** (with country code, e.g., `+1234567890`)
- **Verification code** (sent by Telegram)

Credentials are saved to `config.json` and sessions to `sessions/` directory.

### 5. Start the System

```bash
# Option A: Start both API server + frontend
python run_all.py

# Option B: Start API server only
python backend/api_server.py

# Option C: Start components separately
# Terminal 1:
python backend/api_server.py

# Terminal 2:
cd frontend && npm run dev
```

**Access:**
- Frontend: http://localhost:5173
- API: http://localhost:5000

---

## Usage

### Web Interface (Recommended)

1. **Add Accounts** - Navigate to Accounts page, add your Telegram accounts
2. **Validate Accounts** - Test connections before importing
3. **Upload CSV** - Import page, upload your contact list
4. **Multi-Account Import** - Select accounts, distribute contacts automatically
5. **Scan Replies** - Operations page, detect who responded
6. **Export No-Reply** - Download CSV of contacts who haven't replied (24h/48h/7d)
7. **Organize Folders** - Auto-create folders in Telegram app

### CLI Interface (Advanced)

```bash
# Direct Python access
python backend/matrix.py

# Menu options:
# 1. View Statistics
# 2. Import Dev Contacts (🔵💻)
# 3. Import KOL Contacts (🔵📢)
# 4. Scan for Replies & Update Status
# 5. Organize into Folders
# 6. Configure Rate-Limit Settings
# 0. Exit
```

---

## CSV Format

### Developer Contacts
```csv
group_title,dex_chain,owner
ProjectName,SOL,@username
AnotherProject,ETH,@devuser
```

### KOL Contacts
```csv
telegram,twitter
@koluser,elonmusk
@influencer,vitalikbuterin
```

**Note:** Use the CLASSIFIER tool to clean and chunk large CSVs:
```bash
cd CLASSIFIER
python classifier.py
# Follow interactive prompts
```

---

## Configuration

### Rate-Limiting

Adjust delays to prevent Telegram API bans (configurable via Settings page or `config.json`):

```json
{
  "rate_limit": {
    "batch_size_min": 3,
    "batch_size_max": 7,
    "delay_per_contact_min": 2,
    "delay_per_contact_max": 6,
    "batch_pause_min": 45,
    "batch_pause_max": 90
  }
}
```

**Defaults:**
- Batch size: 3-7 contacts (randomized)
- Per-contact delay: 2-6 seconds (randomized)
- Batch pause: 45-90 seconds (randomized)

### Multi-Account Strategy

Contacts are distributed via **round-robin allocation**:
- 100 contacts, 3 accounts → 34, 33, 33 distribution
- Each account processes independently in parallel
- Results exported to CSV with per-account mapping

---

## Contact Status Encoding

Contacts are tagged with emojis in their Telegram first name:

| Emoji | Type | Status | Description |
|-------|------|--------|-------------|
| 🔵💻 | Dev | Active | Developer who hasn't replied yet |
| 🟡💻 | Dev | Replied | Developer who responded |
| 🔵📢 | KOL | Active | KOL who hasn't replied yet |
| 🟡📢 | KOL | Replied | KOL who responded |

**Auto-update:** When reply detected, emoji changes from blue (🔵) → yellow (🟡)

---

## Folder Organization

MATRIX auto-creates 4 Telegram folders:

1. **🔵💻 Active Devs** - Developers awaiting reply
2. **🟡💻 Replied Devs** - Developers who responded
3. **🔵📢 Active KOLs** - KOLs awaiting reply
4. **🟡📢 Replied KOLs** - KOLs who responded

Contacts automatically move between folders as status updates.

---

## API Endpoints

Full API documentation: [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)

**Key Endpoints:**
- `GET /api/health` - Health check
- `GET /api/accounts` - List all accounts
- `POST /api/accounts/add` - Add new account
- `POST /api/import/devs/multi` - Multi-account import (devs)
- `POST /api/scan-replies` - Scan for replies
- `POST /api/organize-folders` - Organize contacts into folders
- `GET /api/logs` - View operation logs
- `GET /api/audit` - Audit trail

---

## Project Structure

```
MATRIX/
├── backend/                    # Python backend
│   ├── matrix.py              # Core contact manager (2,154 lines)
│   ├── api_server.py          # Flask REST API (1,256 lines)
│   ├── account_manager.py     # SQLite account management (367 lines)
│   └── setup.py               # Initialization script
│
├── frontend/                   # React frontend
│   ├── src/
│   │   ├── pages/             # 9 page components
│   │   ├── components/        # Shared UI components
│   │   ├── services/api.js    # API client (488 lines)
│   │   └── hooks/             # Custom React hooks
│   └── package.json           # Frontend dependencies
│
├── CLASSIFIER/                 # CSV cleaning utility
│   ├── classifier.py          # Clean/chunk CSVs (284 lines)
│   └── cleaned/               # Output directory
│
├── sessions/                   # Telegram session files (encrypted)
├── logs/                       # Operation logs
├── uploads/                    # Uploaded CSV files
├── accounts.db                 # SQLite account database
├── config.json                 # API credentials (not in git)
├── run_all.py                  # Unified launcher
└── README.md                   # This file
```

---

## Security

**Credentials Storage:**
- `config.json` - Telegram API credentials (excluded from git via `.gitignore`)
- `sessions/` - Encrypted Telethon sessions (excluded from git)
- `accounts.db` - SQLite database with account info (excluded from git)

**Best Practices:**
- Never commit `config.json` or session files
- Use environment variables for production deployments
- Regularly rotate API credentials if compromised
- Keep `accounts.db` backed up securely

**Input Validation:**
- Path traversal protection on file uploads
- Parameterized SQL queries (prevents injection)
- Phone number format validation
- CSV file size limits (10MB max)

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'telethon'"
```bash
pip install -r requirements.txt
```

### "Database is locked"
Close any running instances of `matrix.py` or `api_server.py`, then retry.

### "No accounts found"
Add accounts via:
- Frontend: Accounts page → Add Account
- API: `POST /api/accounts/add`
- CLI: `python backend/matrix.py` (first run)

### "FloodWaitError" from Telegram
You've hit Telegram rate limits. Increase delays in Settings page:
- Increase batch pause to 120-180 seconds
- Reduce batch size to 2-4 contacts
- Wait 24 hours before retrying

### Session expired / Re-authentication required
Delete old session file and re-authenticate:
```bash
rm sessions/session_PHONE.session
python backend/matrix.py  # Re-authenticate
```

---

## Development

### Run Tests
```bash
cd backend
python test_backend_complete.py
python test_validation.py
```

### API Development
```bash
# Start API in debug mode
cd backend
FLASK_ENV=development python api_server.py
```

### Frontend Development
```bash
cd frontend
npm run dev
# Frontend runs with hot-reload on localhost:5173
```

### Database Inspection
```bash
sqlite3 accounts.db
.schema              # View table structure
SELECT * FROM accounts;
SELECT * FROM audit_log;
.quit
```

---

## Performance

**Benchmarks (100 contacts):**
- Single-account import: ~8-12 minutes (with default rate-limits)
- Multi-account import (3 accounts): ~3-5 minutes (parallel processing)
- Reply scan: ~10-15 minutes (depends on dialog count)
- Status update: ~5-8 minutes (batch processing)

**Optimization Tips:**
- Use multi-account import for large campaigns (3-5 accounts recommended)
- Adjust rate-limits based on network quality
- Import during off-peak hours to reduce Telegram congestion
- Use CLASSIFIER to pre-chunk CSVs into 100-row files

---

## Roadmap

**Implemented (Production Ready):**
- ✅ Multi-account import with round-robin distribution
- ✅ Reply detection and auto-status updates
- ✅ Folder organization (4 auto-created folders)
- ✅ CSV import/export
- ✅ React web interface
- ✅ REST API
- ✅ SQLite account management
- ✅ Audit trail logging
- ✅ Session persistence

**Planned (Future):**
- ⏳ Emoji validation (verify correctness)
- ⏳ Emoji batch fixing (correct mismatches)
- ⏳ Dialog-driven reply detection (10x faster)
- ⏳ Advanced analytics dashboard
- ⏳ Bulk messaging features
- ⏳ Webhook integrations

See [MATRIX_FUTURE_ROADMAP.md](./MATRIX_FUTURE_ROADMAP.md) for detailed plans.

---

## Documentation

| Document | Purpose |
|----------|---------|
| **README.md** (this file) | Main documentation, quick start, usage |
| **API_DOCUMENTATION.md** | Complete REST API reference |
| **SETUP.md** | Detailed setup instructions |
| **MATRIX_OPERATIONAL_GUIDE.md** | Comprehensive user manual |
| **MATRIX_FUTURE_ROADMAP.md** | Planned features (not implemented) |

**For Developers:**
- Read `backend/matrix.py` docstrings for implementation details
- Review `frontend/src/services/api.js` for API client usage
- Check `account_manager.py` for database schema

---

## Stack

**Backend:**
- Python 3.8+
- Telethon 1.33.0 (Telegram API)
- Flask 2.3.3 (REST API)
- Flask-CORS 4.0.0
- SQLite (account database)

**Frontend:**
- React 19.2.0
- React Router DOM 7.9.6
- Vite 7.2.2 (build tool)
- Tailwind CSS 4.1.17
- Axios 1.13.2 (HTTP client)

**Dev Tools:**
- ESLint (JavaScript linting)
- PostCSS (CSS processing)
- Autoprefixer

---

## License

Proprietary - Internal use only

---

## Support

For issues, bugs, or feature requests:
1. Check existing documentation (SETUP.md, API_DOCUMENTATION.md)
2. Review troubleshooting section above
3. Inspect logs in `logs/` directory
4. Check audit trail: `GET /api/audit`

**Logs Location:**
- API server logs: `logs/matrix_api.log`
- Operation logs: `logs/unified_manager_*.log`
- No-reply exports: `logs/noreply/`

---

## Credits

Built for crypto/blockchain community outreach automation.

**Core Technologies:**
- [Telethon](https://github.com/LonamiWebs/Telethon) - Telegram API client
- [Flask](https://flask.palletsprojects.com/) - Python web framework
- [React](https://reactjs.org/) - Frontend framework
- [Vite](https://vitejs.dev/) - Build tool

**Version:** 1.1
**Last Updated:** December 4, 2025
**Status:** Production Ready

---

## Changelog

### v1.1 (December 4, 2025)

**Bug Fixes:**
- ✅ Fixed "415 Unsupported Media Type" error on contact import
- ✅ Fixed account isolation - each account now has separate backup files and stats
- ✅ Fixed Dashboard backup using wrong account (now uses selected account)
- ✅ Fixed stale cache data when switching between accounts
- ✅ Added helpful error messages for accounts without backups

**Improvements:**
- ✅ Per-account contact caching (prevents cache thrashing with many accounts)
- ✅ Per-account backup files stored in `logs/backups/contacts_{phone}_latest.csv`
- ✅ Cache invalidation on account selection change
- ✅ Unified codebase - `matrix.py` merged into `api_server.py` (web-only, CLI removed)

**Architecture:**
- Backend now ~5000 lines in single `api_server.py` file
- Added `PerAccountCacheManager` class for scalability to hundreds of accounts
- Backup files now properly isolated per account
