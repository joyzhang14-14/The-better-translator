# Discord Translator Bot - Changelog

## v2.3.3 - Fixed Private Main Menu (2025-08-20)

### 🔧 Critical Bug Fix
- **Real Private Main Menu**: Converted `!bot14` from text command to slash command (`/bot14`)
- **True Ephemeral Support**: Main menu is now actually hidden from other users (previous fix didn't work)
- **Improved Architecture**: Slash commands provide better Discord integration and privacy

### 📋 Technical Changes
- **Command Type**: Changed from `@bot.command` to `@bot.tree.command`
- **Parameter Updates**: Changed `ctx` to `interaction: discord.Interaction`
- **Response Method**: Now uses `interaction.response.send_message(..., ephemeral=True)`
- **Message Tracking**: Updated tracking to work with interaction responses
- **Passthrough Commands**: Removed `/bot14` from text command passthrough list
- **Slash Command Sync**: Added automatic slash command synchronization in `setup_hook()`

### ⚠️ Breaking Change
- **Command Usage**: Users now need to use `/bot14` (slash command) instead of `!bot14` (text command)
- **Auto-complete**: Discord will show the command in the auto-complete menu
- **Better UX**: Slash command provides better user experience with descriptions

---

## v2.3.2 - Private Main Menu (2025-08-20)

### 🔒 Privacy Enhancement
- **Private Main Menu**: Main menu (!bot14) is now only visible to the command caller (ephemeral)
- **Improved User Experience**: Other users can no longer see the main menu interface, reducing channel clutter
- **Enhanced Privacy**: Bot interactions are now private by default for better user experience

### 🔧 Technical Changes
- **Ephemeral Response**: Changed from `ctx.reply()` to `ctx.send()` with `ephemeral=True`
- **Message Visibility**: Main menu commands now use Discord's ephemeral message system
- **UI Cleanup**: Removed `mention_author=False` parameter as it's not needed with ephemeral messages

---

## v2.3.1 - Main Menu UX Optimization (2025-08-20)

### 🎨 User Experience Improvements
- **Main Menu Persistence**: Main menu (!bot14) now stays visible for 10 minutes instead of being deleted by sub-menu operations
- **Independent Timeout**: Main menu has its own 10-minute auto-delete timer, separate from sub-menu interactions
- **Smart Message Management**: Sub-menus only clean up their own popup messages, preserving main navigation
- **Consistent Navigation**: Users can return to main menu options while browsing sub-menus

### 🔧 Technical Enhancements
- **New Cleanup Function**: Added `_cleanup_popup_only()` to preserve main menu during navigation
- **Timeout Optimization**: Reduced ErrorSelectionView timeout from 10 hours to 10 minutes
- **Auto-Delete Mechanism**: Enhanced `on_timeout()` with automatic message deletion and cleanup
- **Message Reference Tracking**: Improved message lifecycle management for better resource usage

### 🛠 Message Management Flow
1. **Main Menu Creation**: `!bot14` clears all old messages and creates new main menu (10-min timer)
2. **Sub-Menu Navigation**: Button clicks preserve main menu, only clear previous popup messages  
3. **Auto Cleanup**: Main menu automatically deletes after 10 minutes of inactivity
4. **Reset Behavior**: New `!bot14` command properly cleans up previous main menu

---

## v2.3.0 - Problem Report Management System (2025-08-20)

### 🆕 New Features
- **Problem Report Cloud Storage**: Problem reports now automatically save to persistent cloud storage
- **Admin-Only Management Commands**: Added exclusive admin commands for problem report management
- **One-Click Download**: `!download_problems` - Download all problem reports as JSON file
- **Smart Sync**: `!sync_problems` - Sync cloud data to container local file
- **Safe Deletion**: `!clear_problems` - Delete all problem reports with confirmation dialog
- **Cloud Diagnostics**: `!debug_cloud` - Test cloud connectivity and preview reports
- **DeepL Empty Result Fix**: Enhanced fallback mechanism when DeepL returns empty translations

### 🔒 Security Enhancements
- **Restricted Access**: Admin commands limited to specific user ID (1073555366803165245)
- **Confirmation Dialogs**: Destructive operations require button confirmation
- **Double Validation**: Both command-level and button-level access control

### 🛠 Technical Improvements
- **Persistent Storage**: Problem reports survive container restarts via cloud storage
- **Dual Storage**: Automatic saving to both cloud and local container file
- **Enhanced Debugging**: Comprehensive logging for troubleshooting
- **Error Handling**: Robust error handling with detailed feedback
- **Translation Reliability**: Smart fallback when context translation fails

### 🐛 Bug Fixes
- **Empty Translation Results**: Fixed DeepL returning empty results with context
- **Automatic Fallback**: Context failure now triggers simple translation retry
- **Enhanced Detection**: Better empty/whitespace result detection

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

## v2.2.4 - Critical Stability & Security Audit Release (2025-01-20)

### 🔧 Critical Audit Fixes
1. **Comprehensive Ultra Audit Completion**
   - Deep inspection for double preprocessing issues across all code paths
   - Systematic review of main logic for anomalies and edge cases
   - Implementation of critical security fixes for unsafe configuration access
   - Addition of comprehensive error handling throughout the translation pipeline

2. **Double Preprocessing Elimination**
   - Fixed star patch processing to use raw_original content instead of preprocessed text
   - Ensured all translation calls preserve emojis by using original content
   - Eliminated emoji loss throughout the translation workflow
   - Unified emoji handling across all processing paths

3. **Critical Configuration Security**
   - Replaced all unsafe cfg["key"] access patterns with safe cfg.get("key") calls
   - Added early validation for required configuration fields (channel IDs, webhook URLs)
   - Implemented comprehensive error handling to prevent KeyError crashes
   - Enhanced configuration robustness for production stability

4. **Translation Pipeline Robustness**
   - Added try-catch protection around all translation logic to prevent bot crashes
   - Implemented graceful degradation when translation services fail
   - Added error notification system for failed translations
   - Enhanced logging for debugging translation issues

5. **Star Patch Processing Improvements**
   - Fixed unsafe configuration access in star patch edit functionality
   - Enhanced error handling for message history processing
   - Improved logging and debugging capabilities for patch operations
   - Ensured consistent emoji preservation across all patch operations

6. **Production Stability Enhancements**
   - Eliminated all potential crash points from unsafe configuration access
   - Added comprehensive validation before accessing required configuration
   - Implemented fallback mechanisms for translation failures
   - Enhanced error recovery throughout the bot's operation

---

## v2.2.3 - Admin UI & Dual-Channel Fixes

### 🔒 Admin-Only Button Visibility System
- Term Detection Settings and Permission Settings buttons now only visible to server owners and whitelisted users
- Non-admin users only see Report Bug and Glossary buttons
- Enhanced UI security with dynamic button rendering based on user permissions
- Improved user experience by hiding irrelevant options from regular users

### 🐛 Dual-Channel Translation Bug Fix
- Fixed Chinese channel English input translation: now translates to Chinese (Chinese channel) + sends original English (English channel)
- Fixed English channel behavior to consistently send both translated and original messages
- Resolved missing dual-message behavior for English messages from English channel
- Both channels now have consistent cross-language input handling

---

## v2.2.2 - Complete Whitelist Role Management

### 🔧 Complete Whitelist Role Management System
- Added comprehensive role management submenu under Permission Settings
- Three-tier role management: Add Role, List Roles, Remove Role
- Modal-based role addition with @role mention or ID support
- Dropdown-based role removal with name and ID display
- Real-time role validation and duplicate prevention
- Professional role management workflow matching user management

### 🛠 Enhanced Role Administration
- Support for Discord role mention format (@&role_id)
- Intelligent role ID extraction from mentions and direct input
- Role existence verification before whitelist addition
- Comprehensive error handling for invalid roles
- Detailed logging for all role management actions

### 🎨 Unified Permission Management Interface
- Consistent design pattern between user and role management
- Parallel functionality for both users and roles
- Professional administrative interface
- Enhanced navigation and user experience

---

## v2.2.1 - Enhanced User Management

### 🔧 Enhanced Whitelist User Management
- Added complete user management submenu under Permission Settings
- Three-tier user management: Add User, List Users, Remove User
- Modal-based user addition with @mention or ID support
- Dropdown-based user removal with name and ID display
- Real-time whitelist validation and duplicate prevention
- Enhanced error handling and user feedback

### 🎨 UI/UX Improvements
- Changed "术语表 Glossary" button color to purple (blurple) for better visibility
- Simplified permission setting button labels for cleaner interface
- Improved menu hierarchy with consistent naming convention
- Enhanced user experience with streamlined navigation

### 🔧 Professional Whitelist Workflow
- Industry-standard user management interface
- Comprehensive user verification and validation
- Professional error messages and success notifications
- Detailed logging for administrative actions

---

## v2.2.0 - Redesigned Interface & Permission System

### 🎨 Redesigned Main Menu Interface
- Consolidated glossary functions into single "术语表 Glossary" button with submenu
- Added "权限设置 Permission Settings" button for admin control
- Streamlined interface from 5 buttons to 4 buttons for better organization
- Improved user experience with hierarchical menu structure

### 🔒 Comprehensive Permission Management System
- Added permission settings accessible only to server owners and whitelisted users
- Three-tier permission management: view users, view roles, toggle permission mode
- Granular control over bot access with whitelist and role-based permissions
- Real-time permission mode toggling between restricted and open access

### 🔧 Enhanced Glossary Management Submenu
- Dedicated submenu for all term-related operations
- Add Terms, List Terms, Delete Terms functionality in organized interface
- Improved workflow for term management with better categorization
- Professional terminology replacement (prompt → term throughout interface)

### 📝 Professional Terminology Updates
- Replaced all instances of "prompt" with "term" for professional consistency
- Updated button labels, messages, and descriptions to use industry-standard terminology
- Improved bilingual support with consistent Chinese/English terminology
- Enhanced professional appearance across all UI elements

---

## v2.1.0 - Prompt Detection Toggle System

### 🆕 Major Features Added
1. **Prompt Detection Toggle System**
   - Added 5th button "术语检测设置 prompt detection settings" to main menu
   - Per-guild control for glossary/prompt detection functionality
   - Two modes available:
     * ENABLED (Default): More accurate translation but slower processing
     * DISABLED: Faster translation but potentially less accurate
   - Persistent configuration stored in config.json under guilds.{guild_id}.glossary_enabled
   - Bilingual interface with Chinese/English support
   - Integration with existing popup cleanup system

2. **Enhanced Glossary Processing Control**
   - Smart bypass of glossary processing when disabled for performance
   - Real-time configuration checking in translator.py
   - Proper logging for troubleshooting glossary operations
   - Backward compatibility with existing guilds (default: enabled)

3. **User Interface Improvements**
   - Professional button layout with proper styling
   - Clear status indication for current detection mode
   - Comprehensive user guidance on speed vs accuracy trade-offs
   - Seamless integration with existing command structure

---

## Previous Versions Summary

### Configuration Structure
```json
{
  "guilds": {
    "guild_id": {
      "glossary_enabled": true,  // Default: enabled
      "zh_channel_id": number,
      "en_channel_id": number,
      "zh_webhook_url": "string",
      "en_webhook_url": "string",
      "admin": { ... }
    }
  }
}
```

### Performance Impact
- **ENABLED**: Full glossary processing + GPT judgment (slower, more accurate)
- **DISABLED**: Direct translation without glossary checks (faster, potentially less accurate)

### Compatibility
- Backward compatible with existing guild configurations
- Graceful handling of missing glossary_enabled setting (defaults to true)
- No breaking changes to existing translation workflows

---

## Version Numbering System
- **Major.Minor.Patch** format (e.g., 2.3.0)
- **Minor version (+1)** for major feature additions
- **Patch version (+1)** for bug fixes and small improvements
- Current: **v2.3.3** (Fixed Private Main Menu)