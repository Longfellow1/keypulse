# macOS Sensitive Applications Blacklist Research

**Version:** 1.0  
**Date:** 2026-04-21  
**Purpose:** Default shipping blacklist for KeyPulse activity recorder  
**Scope:** macOS only; omit Windows/Linux-specific apps

## Overview

This document catalogs sensitive macOS applications that should be blacklisted by default in KeyPulse to prevent recording of passwords, private communications, financial transactions, and health data. When an app is blacklisted, entire events are discarded without recording window titles, clipboard content, or accessibility tree data.

---

## 1. Password Managers & Identity (CRITICAL)

| Display Name | Bundle ID | Why Blacklist |
|---|---|---|
| 1Password 7 | `com.agilebits.onepassword7` | Contains master passwords, stored credentials, encrypted vaults |
| 1Password X | `com.agilebits.onepassword-ios` | Password sync app with sensitive auth tokens |
| Bitwarden | `com.bitwarden.desktop` | Vault contains all user credentials and secrets |
| Dashlane | `com.dashlane.mac` | Password vault with personal and financial credentials |
| LastPass | `com.lastpass.lp` | Master password and encrypted credential store |
| Keychain Access | `com.apple.keychainaccess` | System certificate, password, and key management |
| Microsoft Authenticator | `com.microsoft.authenticator` | 2FA codes and backup authentication methods |

**Note:** On Apple Silicon & Intel, 1Password uses `com.agilebits.onepassword7` for desktop; verify via `mdls -name kMDItemCFBundleIdentifier /Applications/1Password\ 7.app`.

---

## 2. Messaging & Communication (CRITICAL)

| Display Name | Bundle ID | Why Blacklist |
|---|---|---|
| WeChat (Tencent) | `com.tencent.wechat` | Private conversations, contacts, payment info |
| QQ | `com.tencent.qq` | Personal messaging and contact lists |
| Telegram | `org.telegram.desktop` | Encrypted messages, group chats, file sharing |
| Signal | `org.signal.signal-desktop` | End-to-end encrypted conversations |
| WhatsApp | `com.whatsapp.messenger` | Private chats, media, contact lists |
| iMessage (Messages) | `com.apple.Messages` | Personal text messages, iCloud sync |
| Slack | `com.tinyspk.slack` | Work chats, files, project discussions |
| Discord | `com.hnc.Discord` | Server chats, DMs, voice/video conversations |
| Wechat for Business (企业微信) | `com.tencent.wxwork` | Enterprise communications and contacts |
| Dingtalk (钉钉) | `com.alibaba.dingtalk` | Corporate messaging, approval workflows |
| Lark (飞书) | `com.bytedance.feishu` | Team collaboration, messages, documents |
| LINE | `jp.naver.line` | Personal and group messaging |
| Kakao Talk | `com.kakao.talk` | Korean messaging platform with payment features |

---

## 3. Banking & Financial (CRITICAL)

| Display Name | Bundle ID | Why Blacklist |
|---|---|---|
| ICBC Mobile Banking | `com.icbc.mobile` | Bank credentials, account balance, transactions |
| Bank of China Mobile | `com.bankofchina.mobilebank` | Account info, fund transfers, personal data |
| Wells Fargo Mobile | `com.wf.wellsfargomobile` | Banking credentials, account access |
| Chase Mobile | `com.chase.mobile` | Bank account login, balances, transactions |
| BofA Mobile Banking | `com.bankofamerica.mobile` | Account numbers, routing, transaction history |
| DBS Bank Mobile | `com.dbs.dbsmbanking` | Asian banking, credentials, transactions |
| Robinhood | `com.robinhoodinc.robinhood` | Portfolio, account balance, trading activity |
| Charles Schwab | `com.schwab.mobile` | Investment account, positions, personal data |
| E-TRADE | `com.etrade.mobile` | Trading records, account statements, holdings |
| Crypto.com | `com.crypto.app` | Cryptocurrency holdings, account balance, trades |
| MetaMask | `io.metamask.mobile` | Ethereum wallet, private keys (via recovery phrase) |
| Ledger Live | `com.ledger.live` | Hardware wallet management, crypto balance |

**Note:** Many banks offer macOS apps in addition to iOS; check each regional bank's App Store listing. Bundle IDs above reflect common iOS/mobile versions; macOS versions may have different IDs.

---

## 4. Health & Medical (STRONG RECOMMENDATION)

| Display Name | Bundle ID | Why Blacklist |
|---|---|---|
| Apple Health | `com.apple.health` | Medical records, vitals, health history (iOS only; not on macOS) |
| MyChart (Epic Systems) | `com.epic.mychart` | Patient portal with medical records and PHI |
| Telemedicine Apps (general) | `*telemedicine*`, `*health*` | Doctor visits, prescriptions, diagnoses |
| Pill Reminder / Med Tracking | `*medication*`, `*pills*` | Personal medication schedule and adherence |
| Mental Health Apps | `com.headspace.meditation` `com.calm.ios` | Mental health data, therapy notes, diagnoses |

**Note:** Health app is iOS/iPadOS only; not available on macOS. Recommend regex matching for health-related third-party apps on macOS.

---

## 5. Private Browsing Mode (SPECIAL HANDLING)

Private/Incognito modes should not be blocked at the app level (user may legitimately use Safari, Chrome, etc.). Instead, implement **window-level detection** to suppress recording during private browsing.

### Detection Strategy for macOS

#### **Safari Private Browsing**
- **Window Title Detection:** Safari private windows display "Private" badge or "[Private]" in tab bar
- **AX Attribute Check:** Use `AXRole` = "AXWindow" and inspect `AXTitle` for "Private" substring
- **Alternative:** Check if window URL bar shows private indicator (⊘ icon)
- **Limitation:** Cannot reliably detect via accessibility APIs alone; title inspection is most reliable

#### **Google Chrome Incognito**
- **Window Title:** Chrome incognito windows show "(Incognito)" suffix in `AXTitle`
- **AX Inspection:** Query child windows for incognito indicator text
- **Alternative:** Check URL bar for "Incognito" watermark via OCR if AX unavailable

#### **Firefox Private Browsing**
- **Window Title:** Firefox private windows show "🔒 Private Browsing" indicator
- **AX Attribute:** Search window hierarchy for "PrivateMode" or "Private" text nodes
- **Fallback:** Window role or subrole may indicate private context

### Implementation Recommendation

```
For each window of Safari/Chrome/Firefox:
  1. Fetch AXTitle and inspect for "Private", "Incognito", "🔒" tokens
  2. Query AXChildren for any text element containing private mode indicators
  3. If private mode detected → suppress event logging for that window
  4. Falls back to recording if no indicator found (conservative approach)
```

**Note:** This is **not** a black-and-white app blacklist but a window-level exclusion rule.

---

## 6. Optional Workspace Blacklist (User-Configurable)

Suggest these as optional defaults that power users may disable (e.g., if they want to track meeting time spent in Zoom):

| Display Name | Bundle ID | Rationale | Default |
|---|---|---|---|
| Mail (Apple Mail) | `com.apple.mail` | Email content is personal; consider context | OFF (opt-in) |
| Outlook | `com.microsoft.Outlook` | Email client; can contain sensitive messages | OFF (opt-in) |
| Gmail / web | `(via browser tab detection)` | Email in browser; harder to blacklist | N/A (browser-level) |
| Calendar (Apple) | `com.apple.iCal` | Contains personal and work schedules | OFF (opt-in) |
| Zoom | `us.zoom.videomeeting` | Video call metadata; user may want to track meeting time | OFF (opt-in) |
| Google Meet | `(via Chrome extension)` | Similar to Zoom | OFF (opt-in) |
| Microsoft Teams | `com.microsoft.Teams` | Corporate communications; often needed for tracking | OFF (opt-in) |

**Recommendation:** Make these opt-out via config file (default = `disabled`), not built-in policy.

---

## 7. Additional Considerations

### System-Level Privacy
- **Accessibility Permissions:** KeyPulse requires AX permissions; respect macOS privacy directives
- **Keychain Access:** Do not attempt to query system keychain for password recovery
- **Screenshot / OCR:** Disable OCR/screenshot in private browser windows to avoid capturing unencrypted content

### Data at Rest
- **Event Deletion:** Blacklisted events must not be written to database—not even as sanitized rows
- **Clipboard Suppression:** Do not log clipboard in password managers or messaging apps
- **Window Title Filtering:** Even if app not blacklisted, consider filtering window titles for keywords: `password`, `login`, `credential`, `secret`

### User Configuration
- Allow users to add custom blacklist entries via `~/.config/keypulse/blacklist.yaml`
- Support glob patterns (e.g., `com.tencent.*` to catch WeChat, QQ, Dingtalk variants)
- Log when blacklist event occurs (debug level: "Dropped event from [bundleID] due to blacklist policy")

---

## 8. Bundle ID Verification Methods

To verify bundle IDs of installed apps on macOS:

```bash
# Get bundle ID for any installed app
mdls -name kMDItemCFBundleIdentifier /Applications/AppName.app

# Or use AppleScript
osascript -e 'id of app "AppName"'

# Or inspect Info.plist directly
cat /Applications/AppName.app/Contents/Info.plist | grep -A1 CFBundleIdentifier
```

**Reference:** [Apple Support: Get the bundle ID for a Mac app](https://support.apple.com/guide/deployment/get-the-bundle-id-for-a-mac-app-dep0af2cd611/web)

---

## 9. Sources & References

### Official Documentation
- [Apple Developer: Bundle IDs](https://developer.apple.com/documentation/appstoreconnectapi/bundle-ids)
- [Apple Support: Bundle IDs for iPhone and iPad Apple apps](https://support.apple.com/guide/deployment/bundle-ids-for-iphone-and-ipad-apple-apps-depece748c41/web)
- [Apple Accessibility API Documentation](https://developer.apple.com/documentation/accessibility/accessibility-api)

### Enterprise MDM / DLP References
- [Hexnode: How to blocklist/allowlist apps on macOS](https://www.hexnode.com/mobile-device-management/help/how-to-blacklist-whitelist-apps-on-macos-devices/)
- [Microsoft Intune: Endpoint protection on macOS](https://learn.microsoft.com/en-us/intune/intune-service/protect/endpoint-protection-macos)
- [Kandji: Application Blocking](https://support.kandji.io/kb/application-blocking)

### Browser Private Mode Detection
- [alexwlchan: Using AppleScript to detect Safari Private Browsing](https://alexwlchan.net/2021/detect-private-browsing/)
- [GitHub: detectIncognito (JS-based detection for Chrome/Safari/Firefox)](https://github.com/Joe12387/detectIncognito)
- [macOS Accessibility API issue: Main browser window detection](https://issues.chromium.org/issues/382525581)

### Privacy & Security Context
- [Intego: Is Private Browsing on Safari Really Private?](https://www.intego.com/mac-security-blog/safari-private-browsing/)
- [Intego: Which browser is most private—Safari, Chrome, or Firefox?](https://www.intego.com/mac-security-blog/safari-chrome-firefox-which-is-the-most-private-browser-for-mac/)

---

## 10. Summary Statistics

| Category | Count | Notes |
|---|---|---|
| Password Managers | 7 | Includes system Keychain |
| Messaging Apps | 13 | Covers global + Asian platforms |
| Banking & Finance | 12 | US, Chinese, Asian banks + crypto |
| Health & Medical | 5 | iOS-focused; few macOS native apps |
| Optional (User-Config) | 7 | Workspace apps: mail, calendar, video calls |
| **Total Recommended for Default Blacklist** | **39** | Core security-critical apps |

---

## 11. Implementation Checklist

- [ ] Parse blacklist from config file (YAML or JSON)
- [ ] Support glob patterns for bundle ID matching
- [ ] Implement window-level private mode detection for browsers
- [ ] Log blacklist events at DEBUG level with reason + bundle ID
- [ ] Add user-facing config option to customize blacklist
- [ ] Test with 5+ apps from each category
- [ ] Document blacklist behavior in user guide and CLI help
- [ ] Provide migration path if user has custom blacklist from v1.x

---

**Last Updated:** 2026-04-21  
**Author:** Security Research Team (KeyPulse)  
**Status:** Draft ready for implementation review
