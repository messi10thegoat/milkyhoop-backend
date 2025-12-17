# Remote Barcode Scanner Architecture

## Executive Summary

Proposal arsitektur untuk fitur **Remote Barcode Scanner** di MilkyHoop, dimana desktop web bertindak sebagai **extended display** dari primary account di mobile web.

---

## 🔒 HARD INVARIANTS (TIDAK BOLEH DILANGGAR)

```
┌─────────────────────────────────────────────────────────────┐
│                    SISTEM CONSTRAINT                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. MAKSIMAL 1 mobile session per user                      │
│  2. MAKSIMAL 1 desktop session per user                     │
│  3. Desktop TIDAK BISA login mandiri                        │
│  4. Desktop SELALU bergantung ke mobile (QR pairing)        │
│  5. Scan HANYA valid jika mobile DAN desktop ONLINE         │
│                                                             │
│  ❌ Tidak ada multi-desktop                                 │
│  ❌ Tidak ada multi-primary                                 │
│  ❌ Tidak ada offline sync                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Tujuan (Goals)

### 1.1 Primary Goal
Memungkinkan user menggunakan **desktop/laptop (tanpa kamera)** untuk operasi POS/Inventory dengan memanfaatkan **kamera HP** sebagai barcode scanner.

### 1.2 Secondary Goals
- Seamless experience tanpa perlu setup kompleks
- Real-time sync antara desktop dan mobile
- Fallback ke local camera jika tersedia (tablet dengan kamera)

---

## 2. Konteks Penting

### 2.1 Authentication Model
```
┌─────────────────────────────────────────────────────────────┐
│                    AUTHENTICATION FLOW                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   MOBILE WEB (Primary)          DESKTOP WEB (Extended)      │
│   ━━━━━━━━━━━━━━━━━━━━          ━━━━━━━━━━━━━━━━━━━━━━      │
│                                                             │
│   ✅ Login mandiri              ❌ TIDAK bisa login mandiri │
│   ✅ Full account access        ✅ Extended display only    │
│   ✅ Has camera                 ❌ Usually no camera        │
│   ✅ Primary session            ✅ Linked to mobile session │
│                                                             │
│   User login di HP    ───────►  Scan QR di desktop          │
│   (username/password)           (WhatsApp Web style)        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Kunci**: Desktop web adalah **perpanjangan layar** dari mobile, bukan session terpisah.

### 2.2 Use Cases

| Use Case | Desktop | Mobile |
|----------|---------|--------|
| POS Kasir | Layar besar untuk UI | Scanner barcode |
| Inventory Check | List produk, stock | Scan untuk lookup |
| Pembelian/Purchase | Input data supplier | Scan barcode produk |
| Registrasi Produk | Form input | Scan barcode baru |

---

## 3. Arsitektur Sistem

### 3.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         MILKYHOOP CLOUD                              │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    WebSocket Server                          │    │
│  │        (Reuse existing - tambah namespace remote_scan:*)     │    │
│  │                                                              │    │
│  │  ┌─────────────────┐    ┌─────────────────┐                 │    │
│  │  │   Room:         │    │   Room:         │                 │    │
│  │  │   user_A        │    │   user_B        │                 │    │
│  │  │                 │    │                 │                 │    │
│  │  │  📱 mobile (1)  │    │  📱 mobile (1)  │  ← MAX 1        │    │
│  │  │  🖥️ desktop (1) │    │  🖥️ desktop (1) │  ← MAX 1        │    │
│  │  └─────────────────┘    └─────────────────┘                 │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Session Manager                           │    │
│  │  • Enforces 1 mobile + 1 desktop per user                   │    │
│  │  • Validates mobile-desktop pairing                         │    │
│  │  • Rejects scan_request if either device offline            │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
            ┌───────────────┐   ┌───────────────┐
            │  MOBILE WEB   │   │  DESKTOP WEB  │
            │  (Primary)    │   │  (Extended)   │
            │               │   │               │
            │  📱 Camera    │   │  🖥️ Large UI  │
            │  🔐 Auth      │   │  📊 POS View  │
            │  🎯 Scanner   │   │  ⏳ Waiting   │
            └───────────────┘   └───────────────┘
```

### 3.2 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FRONTEND COMPONENTS                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  MOBILE WEB                          DESKTOP WEB                     │
│  ━━━━━━━━━━                          ━━━━━━━━━━━                     │
│                                                                      │
│  ┌─────────────────────┐            ┌─────────────────────┐         │
│  │ RemoteScanListener  │◄──────────►│ RemoteScanTrigger   │         │
│  │                     │  WebSocket │                     │         │
│  │ • Listens for scan  │            │ • Request scan      │         │
│  │   requests          │            │ • Show "waiting"    │         │
│  │ • Opens scanner     │            │ • Receive result    │         │
│  │ • Sends result back │            │                     │         │
│  └─────────────────────┘            └─────────────────────┘         │
│           │                                   │                      │
│           ▼                                   ▼                      │
│  ┌─────────────────────┐            ┌─────────────────────┐         │
│  │ FullscreenBarcode   │            │ ScanResultHandler   │         │
│  │ Scanner             │            │                     │         │
│  │                     │            │ • Process barcode   │         │
│  │ • Camera access     │            │ • Update UI         │         │
│  │ • Barcode detection │            │ • Add to cart, etc  │         │
│  └─────────────────────┘            └─────────────────────┘         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.3 WebSocket Message Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                    REMOTE SCAN SEQUENCE                             │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  DESKTOP                    SERVER                    MOBILE        │
│     │                         │                         │           │
│     │  1. scan_request        │                         │           │
│     │ ───────────────────────►│                         │           │
│     │   {                     │                         │           │
│     │     type: "scan_req",   │  2. forward to mobile   │           │
│     │     context: "pos",     │ ────────────────────────►           │
│     │     request_id: "abc"   │                         │           │
│     │   }                     │                         │           │
│     │                         │                         │           │
│     │                         │                    [Scanner Opens]  │
│     │                         │                    [User Scans]     │
│     │                         │                         │           │
│     │                         │  3. scan_result         │           │
│     │  4. forward to desktop  │ ◄────────────────────────           │
│     │ ◄───────────────────────│   {                     │           │
│     │   {                     │     type: "scan_res",   │           │
│     │     barcode: "899...",  │     request_id: "abc",  │           │
│     │     format: "EAN_13"    │     barcode: "899..."   │           │
│     │   }                     │   }                     │           │
│     │                         │                         │           │
│  [Process barcode]            │                    [Scanner Closes] │
│     │                         │                         │           │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Structures

### 4.1 WebSocket Messages

```typescript
// Desktop → Server: Request scan
interface ScanRequest {
  type: 'scan_request';
  request_id: string;        // UUID untuk tracking
  context: 'pos' | 'inventory' | 'purchase' | 'registration';
  metadata?: {
    expected_format?: string[];  // ['EAN_13', 'CODE_128']
    prompt_text?: string;        // "Scan produk untuk ditambahkan"
  };
}

// Server → Mobile: Forward scan request
interface ScanRequestForward {
  type: 'scan_request_forward';
  request_id: string;
  context: string;
  from_device: {
    device_id: string;
    device_type: 'desktop' | 'tablet';
  };
  metadata?: object;
}

// Mobile → Server: Scan result
interface ScanResult {
  type: 'scan_result';
  request_id: string;
  success: boolean;
  barcode?: string;
  format?: string;
  error?: string;  // 'cancelled' | 'camera_error' | 'timeout'
}

// Server → Desktop: Forward result
interface ScanResultForward {
  type: 'scan_result_forward';
  request_id: string;
  success: boolean;
  barcode?: string;
  format?: string;
  error?: string;
}

// Connection status
interface DeviceStatus {
  type: 'device_status';
  mobile_connected: boolean;
  mobile_device_id?: string;
  mobile_device_name?: string;  // "iPhone 12" or browser info
}
```

### 4.2 Session Pairing

```typescript
interface PairedSession {
  tenant_id: string;
  user_id: string;

  primary_device: {
    device_id: string;
    device_type: 'mobile';
    connected_at: Date;
    last_activity: Date;
  };

  extended_devices: Array<{
    device_id: string;
    device_type: 'desktop' | 'tablet';
    connected_at: Date;
    last_activity: Date;
    paired_via: 'qr_scan';  // How it was paired
  }>;
}
```

---

## 5. UI/UX Flow

### 5.1 Desktop: Scan Button States

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DESKTOP SCAN BUTTON STATES                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  STATE 1: Mobile Connected                                          │
│  ┌─────────────────────────────┐                                    │
│  │  📱 Scan via Mobile         │  ← Green indicator                 │
│  │  iPhone tersambung          │                                    │
│  └─────────────────────────────┘                                    │
│                                                                      │
│  STATE 2: Waiting for Scan                                          │
│  ┌─────────────────────────────┐                                    │
│  │  ⏳ Menunggu scan...        │  ← Animated                        │
│  │  Scan di HP Anda            │                                    │
│  └─────────────────────────────┘                                    │
│                                                                      │
│  STATE 3: Mobile Not Connected                                      │
│  ┌─────────────────────────────┐                                    │
│  │  📵 Mobile tidak tersambung │  ← Gray/disabled                   │
│  │  Buka MilkyHoop di HP       │                                    │
│  └─────────────────────────────┘                                    │
│                                                                      │
│  STATE 4: Local Camera Available (Tablet)                           │
│  ┌─────────────────────────────┐                                    │
│  │  📷 Scan Barcode            │  ← Normal button                   │
│  │  [Gunakan kamera lokal]     │                                    │
│  └─────────────────────────────┘                                    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Mobile: Remote Scan Notification

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MOBILE SCAN REQUEST UI                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  OPTION A: Auto-open Scanner (Recommended)                          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                          │
│  When scan request received:                                        │
│  • Vibrate phone                                                    │
│  • Auto-open FullscreenBarcodeScanner                              │
│  • Show context: "Scan untuk POS Desktop"                          │
│  • After scan: auto-close & send result                            │
│                                                                      │
│  OPTION B: Notification + Manual Open                               │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                             │
│  When scan request received:                                        │
│  • Show toast/banner: "Desktop meminta scan"                       │
│  • User taps to open scanner                                       │
│  • More control but extra step                                     │
│                                                                      │
│  RECOMMENDATION: Option A for speed, with Option B as setting      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Implementation Phases

### Phase 1: Foundation (Backend)
- [ ] Extend existing WebSocket infrastructure
- [ ] Add scan_request/scan_result message handlers
- [ ] Session pairing validation
- [ ] Message routing (desktop ↔ mobile)

### Phase 2: Mobile Components
- [ ] RemoteScanListener service (background listener)
- [ ] Auto-open scanner on request
- [ ] Send result back via WebSocket
- [ ] Handle cancel/timeout

### Phase 3: Desktop Components
- [ ] RemoteScanTrigger component
- [ ] Mobile connection status indicator
- [ ] "Waiting for scan" UI
- [ ] Result handler integration

### Phase 4: Integration
- [ ] POS/SalesTransaction integration
- [ ] Inventory lookup integration
- [ ] Purchase/Pembelian integration
- [ ] Barcode registration integration

### Phase 5: Polish
- [ ] Offline handling
- [ ] Reconnection logic
- [ ] Error states
- [ ] Sound/vibration feedback

---

## 7. Technical Considerations

### 7.1 Existing Infrastructure
- WebSocket sudah ada untuk QR Login flow
- BarcodeScanner utility sudah production-ready
- Session management sudah multi-device aware

### 7.2 Security
- Scan request harus validated (same tenant, same user)
- Rate limiting untuk prevent spam
- Timeout untuk stale requests (30 detik)

### 7.3 Edge Cases (Resolved)
- Mobile app di background → **Desktop button disabled, UI: "Buka MilkyHoop di HP"**
- Multiple desktop sessions → **N/A - sistem hanya izinkan 1 desktop**
- Mobile loses connection mid-scan → **Fail fast, desktop minta scan ulang**
- Desktop disconnects before result → **Server drop result, job failed**

### 7.4 Performance
- WebSocket latency target: < 100ms
- Scanner open time: < 500ms
- Total round-trip: < 3 seconds

---

## 8. ✅ Expert Review Decisions

### 8.1 WebSocket vs Polling
**KEPUTUSAN: WebSocket existing SUDAH CUKUP**

- Reuse existing WebSocket connection (dari QR Login)
- Pattern identik: desktop request → mobile action → desktop response
- Latency requirement (<3 detik) tidak masuk akal pakai polling
- ❌ Jangan bikin channel baru
- ❌ Jangan REST + polling
- ✅ Tambahkan message namespace: `type: 'remote_scan:*'`

### 8.2 Push Notification
**KEPUTUSAN: TIDAK perlu sekarang**

- Mobile **harus online & aktif** supaya desktop bisa dipakai
- Kalau mobile mati/background → desktop **memang seharusnya degraded**
- Ini acceptable UX (WhatsApp Web juga begitu)
- Jika mobile WS disconnect → desktop scan button disabled
- UI copy: "Buka MilkyHoop di HP untuk scan barcode"

### 8.3 Multi-Desktop Routing
**KEPUTUSAN: Pertanyaan GUGUR**

- Sistem **hanya mengizinkan 1 desktop aktif** per user
- Session manager sudah enforce ini
- Tidak perlu routing logic
- Tidak perlu device priority
- Implementasi: `IF desktop_connected = false THEN reject scan_request`

### 8.4 Offline Sync
**KEPUTUSAN: Tidak ada sync. Fail fast.**

- Desktop trigger scan → Mobile scan berhasil → Desktop disconnect
- Server **drop result** (tidak disimpan)
- Job dianggap failed
- Desktop UI minta scan ulang
- ❌ Jangan simpan hasil scan untuk "nanti disinkronkan"
- Ini bukan chat app, ini **action-based system**

### 8.5 Alternative Approach
**KEPUTUSAN: Model sekarang adalah SWEET SPOT**

Alternatif yang ditolak:
- ❌ Desktop kirim barcode manual dari mobile → UX buruk, error tinggi
- ❌ Desktop buka camera HP via WebRTC → Ribet, permission hell
- ❌ Upload foto barcode → Lambat, tidak realtime

Model sekarang:
- ✅ Realtime
- ✅ Deterministic
- ✅ Familiar (WhatsApp mental model)
- ✅ Aman

---

## 9. Appendix

### A. Existing Components Reference
- `/frontend/web/src/components/FullscreenBarcodeScanner.tsx` - Scanner UI
- `/frontend/web/src/utils/BarcodeScanner.ts` - Scanner utility
- `/backend/services/auth_service/` - WebSocket untuk QR login
- `/frontend/web/src/components/QRScanner.tsx` - QR scanner untuk login

### B. Similar Patterns
- WhatsApp Web (QR pairing + message sync)
- Telegram Web (session mirroring)
- Discord (multi-device with primary)

---

## 🏁 VERDICT

```
┌─────────────────────────────────────────────────────────────┐
│                    REVIEW STATUS: ✅ APPROVED                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ✅ Arsitektur SUDAH CUKUP                                  │
│  ✅ Tidak over-engineered                                   │
│  ✅ Aman                                                    │
│  ✅ Cepat diimplementasi                                    │
│  ✅ UX masuk akal untuk UMKM / kasir                        │
│                                                             │
│  Ini BUKAN konsep mentah, ini SIAP DIBANGUN.               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Next Step**: Implementasi Phase 1 (Backend WebSocket handlers)

---

## 📝 Final Review Notes (Architect Sign-off)

**Status**: ✅ APPROVED FOR IMPLEMENTATION

**Confirmation**:
1. ✅ Invariant konsisten & ditegakkan (1 mobile + 1 desktop)
2. ✅ WebSocket reuse = keputusan tepat
3. ✅ Fail-fast philosophy tepat (action system, bukan messaging)
4. ✅ UX trade-off sadar (desktop mati kalau mobile off = by design)
5. ✅ Scope terjaga (tidak kebablasan ke WebRTC/push notif)

**Optional Improvement (Phase 5)**:
- Tambahkan server-side scan timeout guard (30s)
- Kirim explicit `scan_timeout` event supaya state desktop selalu bersih

**Final Verdict**:
> 👉 **SIAP DIBANGUN. Lanjut Phase 1 tanpa ragu.**
