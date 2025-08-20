# Changelog

## v2.3.0 - Problem Report Management System (2025-08-20)

### 🆕 New Features
- **Problem Report Cloud Storage**: Problem reports now automatically save to persistent cloud storage
- **Admin-Only Management Commands**: Added exclusive admin commands for problem report management
- **One-Click Download**: `!download_problems` - Download all problem reports as JSON file
- **Smart Sync**: `!sync_problems` - Sync cloud data to container local file
- **Safe Deletion**: `!clear_problems` - Delete all problem reports with confirmation dialog
- **Cloud Diagnostics**: `!debug_cloud` - Test cloud connectivity and preview reports

### 🔒 Security Enhancements
- **Restricted Access**: Admin commands limited to specific user ID (1073555366803165245)
- **Confirmation Dialogs**: Destructive operations require button confirmation
- **Double Validation**: Both command-level and button-level access control

### 🛠 Technical Improvements
- **Persistent Storage**: Problem reports survive container restarts via cloud storage
- **Dual Storage**: Automatic saving to both cloud and local container file
- **Enhanced Debugging**: Comprehensive logging for troubleshooting
- **Error Handling**: Robust error handling with detailed feedback

### 📝 Problem Report Workflow
1. **Auto-Save**: Reports automatically save to cloud when submitted
2. **Manual Download**: Use `!download_problems` to get local copy
3. **Admin Management**: Full control with restricted access commands

### 🔧 Commands Added
- `!download_problems` - Download problem reports file
- `!sync_problems` - Sync from cloud to container
- `!clear_problems` - Delete all reports (with confirmation)
- `!debug_cloud` - Test cloud storage and preview

---

## v2.2.4 - Previous Version
- Enhanced problem report debugging
- Comprehensive error logging
- Path resolution fixes