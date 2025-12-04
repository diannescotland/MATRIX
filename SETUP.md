# MATRIX System Setup

Complete initialization guide for the MATRIX multi-account management system.

## Quick Setup (One Command)

```bash
python backend/setup.py
```

This single command will:
1. ✅ Check Python version (3.8+ required)
2. ✅ Install all Python dependencies (telethon, flask, flask-cors)
3. ✅ Initialize the accounts database (`accounts.db`)
4. ✅ Migrate existing sessions to the database
5. ✅ Create necessary directories (sessions/, logs/, logs/noreply/, uploads/)
6. ✅ Verify all imports work correctly
7. ✅ Check configuration status

## What Gets Created

After running `setup.py`, you'll have:

```
MATRIX/
├── accounts.db              # SQLite database for account management
├── config.json              # Your API credentials (if exists)
├── sessions/                # Telegram session files
├── logs/                    # Operation logs
│   └── noreply/            # No-reply CSV exports
└── uploads/                # Uploaded CSV files
```

## First-Time Configuration

If `config.json` doesn't exist, you'll need to set up API credentials:

```bash
python backend/matrix.py
```

When prompted:
1. Enter your **API ID** (from https://my.telegram.org/apps)
2. Enter your **API Hash**
3. Enter your **phone number** (e.g., +1234567890)
4. Enter the verification code from Telegram

This creates `config.json` and your first session.

## Starting the System

### Option 1: API Server Only (Python)

```bash
python backend/api_server.py
```

Server runs on `http://localhost:5000`

### Option 2: Full System (API + Frontend)

```bash
python run_all.py
```

Starts both:
- API server: `http://localhost:5000`
- Frontend: `http://localhost:3000` (requires `npm install` in frontend/)

## Adding Accounts

### Method 1: Via API

```python
import requests

response = requests.post('http://localhost:5000/api/accounts/add', json={
    'phone': '+1234567890',
    'name': 'My Account',
    'api_id': 12345678,
    'api_hash': 'your_hash'
})
```

### Method 2: Via Frontend

1. Start the system
2. Go to **Accounts** page
3. Click **+ Add Account**
4. Fill in details and click **Add Account**
5. Click **Validate** to test

### Method 3: Via CLI

```bash
python backend/matrix.py
# Follow prompts - account is automatically added to database
```

## Using Multi-Account Features

### Import Contacts to Multiple Accounts

1. Go to **Import** page
2. Upload your CSV file
3. Select **"Multiple Accounts"** radio button
4. Check the accounts you want to use
5. Click **Start Import**

Contacts will be **equally distributed** across selected accounts.

### Export Import Results

After multi-account import, download the results CSV showing:
- Which username was added to which account
- Status of each import
- Timestamp

### Scan for No-Reply Contacts

1. Go to **Operations** page
2. Select **"Scan & Update Status"**
3. Choose timeframe: **24h**, **48h**, or **7 days**
4. Check **"Export to CSV"** (enabled by default)
5. Click **Start Operation**

CSV files will be generated:
- `noreplyDEV_24h.csv` (or 48h/7d)
- `noreplyKOL_24h.csv` (or 48h/7d)

Files are saved to `logs/noreply/` directory.

## Troubleshooting

### "ModuleNotFoundError"

**Solution:** Run setup again:
```bash
python backend/setup.py
```

### "Database is locked"

**Solution:** Close any running Python scripts or API server, then retry.

### "No accounts found"

**Solution:** Add accounts via:
- Frontend: Accounts page
- API: POST /api/accounts/add
- CLI: python backend/matrix.py

### "API credentials not configured"

**Solution:** Run `python backend/matrix.py` once to set up credentials.

## Verification

After setup, verify everything works:

```bash
# Test database
python -c "from account_manager import get_all_accounts; print(get_all_accounts())"

# Test API server
python backend/api_server.py
# In another terminal:
curl http://localhost:5000/api/health
```

## Next Steps

1. ✅ Run `python backend/setup.py`
2. ✅ Configure API credentials (if needed)
3. ✅ Add accounts via frontend or API
4. ✅ Validate accounts
5. ✅ Start importing contacts!

For detailed usage, see `MATRIX_OPERATIONAL_GUIDE.md`
