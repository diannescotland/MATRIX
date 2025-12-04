# MATRIX Next Session - Future Improvements

## Completed (December 4, 2025)
- [x] Unified api_server.py and matrix.py into single file
- [x] Removed CLI menu (web-only now)
- [x] Rate limit handling improved
- [x] Multi-account support working
- [x] Reply scanning fixed

## File Structure After Unification

```
backend/
├── api_server.py           # UNIFIED FILE (~5000 lines)
│   ├── Flask/SocketIO setup
│   ├── TelegramRateLimitError class
│   ├── Global constants (CONFIG_FILE, SESSIONS_DIR, LOGS_DIR)
│   ├── Utility functions (load_config, save_config, etc.)
│   ├── AccountLockManager class
│   ├── UnifiedContactManager class (~2000 lines)
│   ├── get_manager() and get_manager_for_account()
│   ├── REST API endpoints
│   └── Main entry point
├── account_manager.py      # Database CRUD for accounts
├── api_server.py.backup    # Backup before unification
└── matrix.py.backup        # Backup of original matrix.py
```

## Future Tasks

### High Priority
1. **Add progress WebSocket for all operations**
   - Currently only multi-account ops use WebSocket
   - Single-account scan/backup should emit progress too

2. **Improve error messages**
   - More descriptive rate limit messages
   - Better session corruption handling

3. **Add operation cancellation**
   - Cancel button works for multi-account ops
   - Need to add for single-account ops

### Medium Priority
4. **Add contact deduplication**
   - Check for duplicate usernames before import
   - Merge duplicate entries

5. **Batch contact updates**
   - Update multiple contacts at once
   - Reduce API calls

6. **Add retry logic for failed imports**
   - Track failed contacts
   - Retry option after completion

### Low Priority
7. **Add contact search endpoint**
   - Search contacts by name/username
   - Filter by type/status

8. **Add export formats**
   - JSON export option
   - Excel export option

9. **Add scheduled operations**
   - Schedule daily backup
   - Schedule periodic scan

## Running the Server

```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
python backend/api_server.py
```

The server runs on `http://localhost:5000`

## Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/stats` | GET | Contact statistics |
| `/api/accounts` | GET | List all accounts |
| `/api/scan-replies` | POST | Scan for replies |
| `/api/backup-contacts` | POST | Backup contacts |
| `/api/import/devs` | POST | Import developers |
| `/api/import/kols` | POST | Import KOLs |

## Notes for Next Session

1. **Backup files preserved**: `api_server.py.backup` and `matrix.py.backup` contain the original code before unification

2. **Rate limits are critical**: Never reduce rate limit delays without explicit user permission

3. **Session management**: Always disconnect clients in finally blocks

4. **Database**: Using WAL mode for SQLite to reduce lock contention
