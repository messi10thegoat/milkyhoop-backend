/**
 * ZXing Barcode Scanner Worker
 *
 * Features:
 * - ImageBitmap transfer (zero-copy from main thread)
 * - OffscreenCanvas rendering
 * - Grayscale preprocessing in-place
 * - Multi-format barcode detection
 *
 * Note: This worker uses @zxing/library which is bundled separately.
 * The main thread should import from @zxing/browser.
 */

let zxingReader = null;
let isInitialized = false;

// Supported barcode formats mapping
const FORMAT_MAP = {
  'ean_13': 'EAN_13',
  'ean_8': 'EAN_8',
  'upc_a': 'UPC_A',
  'upc_e': 'UPC_E',
  'code_128': 'CODE_128',
  'code_39': 'CODE_39',
  'code_93': 'CODE_93',
  'codabar': 'CODABAR',
  'itf': 'ITF',
  'qr_code': 'QR_CODE',
  'data_matrix': 'DATA_MATRIX',
  'aztec': 'AZTEC',
  'pdf417': 'PDF_417'
};

/**
 * Initialize the decoder
 * For @zxing/browser, the actual decoding happens via MultiFormatReader
 * which we'll instantiate here
 */
async function initDecoder(formats = []) {
  if (isInitialized) return true;

  try {
    // Worker doesn't have direct access to @zxing/library
    // We'll do pixel processing here and return to main thread for actual decode
    // OR we can use a simpler approach: just preprocess and return ImageData

    isInitialized = true;
    console.log('[ZXing Worker] Initialized with formats:', formats);
    return true;
  } catch (err) {
    console.error('[ZXing Worker] Init failed:', err);
    return false;
  }
}

/**
 * Process ImageBitmap and extract grayscale ImageData
 * Returns processed data for decoding
 */
function processImage(imageBitmap) {
  const w = imageBitmap.width;
  const h = imageBitmap.height;

  // Check OffscreenCanvas support
  if (typeof OffscreenCanvas === 'undefined') {
    console.error('[ZXing Worker] OffscreenCanvas not supported');
    return null;
  }

  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(imageBitmap, 0, 0);

  const imageData = ctx.getImageData(0, 0, w, h);
  const data = imageData.data;

  // Grayscale conversion in-place (fast luminance formula)
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    // ITU-R BT.601 luma coefficients
    const y = (r * 0.299 + g * 0.587 + b * 0.114) | 0;
    data[i] = data[i + 1] = data[i + 2] = y;
  }

  return {
    data: imageData.data,
    width: w,
    height: h
  };
}

/**
 * Convert luminance data to binary hint for ZXing
 * This creates a simple thresholded binary representation
 */
function createLuminanceSource(processedData) {
  const { data, width, height } = processedData;

  // Extract just the luminance values (every 4th byte since we made RGB = Y)
  const luminances = new Uint8ClampedArray(width * height);
  for (let i = 0; i < luminances.length; i++) {
    luminances[i] = data[i * 4]; // R channel (which equals Y after grayscale)
  }

  return {
    luminances,
    width,
    height
  };
}

// Message handler
self.onmessage = async (ev) => {
  const msg = ev.data;

  if (msg.type === 'init') {
    try {
      await initDecoder(msg.formats || []);
      self.postMessage({ type: 'ready' });
    } catch (err) {
      console.error('[ZXing Worker] Init error:', err);
      self.postMessage({ type: 'error', reason: String(err) });
    }
    return;
  }

  if (msg.type === 'decode') {
    const imageBitmap = msg.image;

    try {
      // Process image to grayscale
      const processed = processImage(imageBitmap);
      imageBitmap.close(); // Release memory

      if (!processed) {
        self.postMessage({ type: 'result', result: null });
        return;
      }

      // Create luminance source for ZXing
      const luminanceSource = createLuminanceSource(processed);

      // Send back processed data for main thread to decode
      // (Since @zxing/browser works better in main thread)
      self.postMessage({
        type: 'processed',
        luminanceSource: luminanceSource,
        width: processed.width,
        height: processed.height
      }, [luminanceSource.luminances.buffer]); // Transfer buffer

    } catch (err) {
      try { imageBitmap.close(); } catch (e) {}
      console.error('[ZXing Worker] Decode error:', err);
      self.postMessage({ type: 'result', result: null });
    }
    return;
  }

  if (msg.type === 'decode-raw') {
    // Direct decode from ImageData (if passed from main thread)
    const { imageData, width, height } = msg;

    try {
      // Process to grayscale if not already
      const data = imageData.data || imageData;

      // Create luminance array
      const luminances = new Uint8ClampedArray(width * height);
      for (let i = 0; i < luminances.length; i++) {
        const idx = i * 4;
        const r = data[idx];
        const g = data[idx + 1];
        const b = data[idx + 2];
        luminances[i] = (r * 0.299 + g * 0.587 + b * 0.114) | 0;
      }

      self.postMessage({
        type: 'processed',
        luminanceSource: { luminances, width, height },
        width,
        height
      }, [luminances.buffer]);

    } catch (err) {
      console.error('[ZXing Worker] Raw decode error:', err);
      self.postMessage({ type: 'result', result: null });
    }
  }
};

console.log('[ZXing Worker] Loaded');
