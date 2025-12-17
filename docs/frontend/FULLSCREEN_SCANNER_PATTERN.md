# Fullscreen Scanner UI Pattern

Reusable pattern untuk fullscreen camera/scanner UI di mobile web.

**Reference:** `frontend/web/src/components/QRScanner.tsx`

---

## Quick Start

Copy-paste pattern ini untuk membuat fullscreen scanner:

```tsx
<div className="fixed inset-0 z-50 bg-black">
  {/* Camera background */}
  <video
    ref={videoRef}
    autoPlay playsInline muted
    className="absolute inset-0 w-full h-full object-cover"
  />

  {/* Close button */}
  <button className="absolute top-4 right-4 z-20 w-11 h-11 rounded-full
                     bg-neutral-900/60 backdrop-blur-md
                     flex items-center justify-center">
    <XIcon className="w-6 h-6 text-white" />
  </button>

  {/* Scanning frame */}
  <div className="absolute inset-0 flex items-center justify-center z-10">
    <ScanningFrame />
  </div>

  {/* Status button */}
  <div className="absolute bottom-8 inset-x-4 flex justify-center z-10">
    <div className="px-6 py-4 rounded-full bg-neutral-900/60 backdrop-blur-md">
      <span className="text-white font-medium">Status text here</span>
    </div>
  </div>

  {/* Animation keyframes */}
  <style>{`
    @keyframes scan-breathe {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.08); }
    }
  `}</style>
</div>
```

---

## 1. Glassmorphism Button

Style standar untuk floating buttons di atas camera:

```tsx
className="bg-neutral-900/60 backdrop-blur-md"
```

### Circle Button (Close, Action)
```tsx
<button className="w-11 h-11 rounded-full
                   bg-neutral-900/60 backdrop-blur-md
                   flex items-center justify-center
                   active:bg-neutral-800/60 transition-colors">
  <svg className="w-6 h-6 text-white" .../>
</button>
```

### Pill Button (Status, Nav)
```tsx
<div className="px-6 py-4 rounded-full bg-neutral-900/60 backdrop-blur-md">
  <span className="text-white font-medium">Button Text</span>
</div>
```

### Action Button (Retry, Submit)
```tsx
<button className="px-6 py-3 rounded-full bg-neutral-900/60 backdrop-blur-md
                   text-white font-medium">
  Action
</button>
```

---

## 2. Fullscreen Layout

### Container
```tsx
<div className="fixed inset-0 z-50 bg-black">
```

### Video Background
```tsx
<video
  ref={videoRef}
  autoPlay
  playsInline
  muted
  className="absolute inset-0 w-full h-full object-cover"
/>
```

### Z-Index Hierarchy
| Layer | z-index | Usage |
|-------|---------|-------|
| Video | (base) | Camera background |
| Scanning frame | z-10 | Corner brackets |
| Status button | z-10 | Bottom nav |
| Close button | z-20 | Top controls |
| Overlays | z-20 | Error, Success, etc |

---

## 3. Corner Bracket Scanning Frame

```tsx
<div
  className="w-64 h-64 relative"
  style={{ animation: 'scan-breathe 1s ease-in-out infinite' }}
>
  {/* Top-left */}
  <div className="absolute top-0 left-0 w-12 h-12
                  border-t-4 border-l-4 border-white rounded-tl-2xl" />
  {/* Top-right */}
  <div className="absolute top-0 right-0 w-12 h-12
                  border-t-4 border-r-4 border-white rounded-tr-2xl" />
  {/* Bottom-left */}
  <div className="absolute bottom-0 left-0 w-12 h-12
                  border-b-4 border-l-4 border-white rounded-bl-2xl" />
  {/* Bottom-right */}
  <div className="absolute bottom-0 right-0 w-12 h-12
                  border-b-4 border-r-4 border-white rounded-br-2xl" />
</div>
```

### Customization
| Property | Default | Options |
|----------|---------|---------|
| Size | `w-64 h-64` | `w-48 h-48`, `w-72 h-72` |
| Corner size | `w-12 h-12` | Proportional to frame |
| Border | `border-4` | `border-2`, `border-[3px]` |
| Color | `border-white` | `border-purple-500` |
| Radius | `rounded-tl-2xl` | `rounded-tl-3xl` |

---

## 4. Breathing Animation

```css
@keyframes scan-breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.08); }
}
```

### Usage
```tsx
style={{ animation: 'scan-breathe 1s ease-in-out infinite' }}
```

### Customization
| Property | Default | Options |
|----------|---------|---------|
| Duration | `1s` | `0.8s` (faster), `1.5s` (slower) |
| Scale | `1.08` | `1.05` (subtle), `1.1` (pronounced) |
| Easing | `ease-in-out` | `ease`, `linear` |

---

## 5. State Overlays

### Backdrop
```tsx
<div className="absolute inset-0 z-20 flex items-center justify-center
                bg-black/70 backdrop-blur-sm">
```

### Loading Spinner
```tsx
<div className="animate-spin w-12 h-12 border-4
                border-white/30 border-t-white rounded-full" />
```

### Success Icon
```tsx
<div className="w-20 h-20 bg-green-500/20 rounded-full
                flex items-center justify-center">
  <svg className="w-10 h-10 text-green-400">
    <path d="M5 13l4 4L19 7" />
  </svg>
</div>
```

### Error Icon
```tsx
<div className="w-20 h-20 bg-red-500/20 rounded-full
                flex items-center justify-center">
  <svg className="w-10 h-10 text-red-400">
    <path d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
</div>
```

---

## 6. Camera Access

```tsx
const startCamera = async () => {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: 'environment', // Back camera
      width: { ideal: 1280 },
      height: { ideal: 720 }
    }
  });

  videoRef.current.srcObject = stream;
  await videoRef.current.play();
};

const stopCamera = () => {
  stream.getTracks().forEach(track => track.stop());
  videoRef.current.srcObject = null;
};
```

---

## 7. Full Example Component

See `frontend/web/src/components/QRScanner.tsx` for complete implementation with:
- Camera initialization
- QR code scanning with jsQR
- Manual input fallback
- Approval flow
- Error handling
- All state overlays
