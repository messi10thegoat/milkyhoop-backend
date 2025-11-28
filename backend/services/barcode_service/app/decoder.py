from pyzbar.pyzbar import decode as pyzbar_decode
import numpy as np

def decode_barcode(image: np.ndarray) -> list[dict]:
    """
    Decode barcodes from preprocessed image using pyzbar.

    Args:
        image: Grayscale numpy array (preprocessed)

    Returns:
        List of decoded barcodes with format:
        [{text: str, type: str, rect: {left, top, width, height}}]
    """
    results = pyzbar_decode(image)

    output = []
    for r in results:
        # Decode bytes to string
        try:
            text = r.data.decode('utf-8', errors='replace')
        except:
            # If decode fails, return hex representation
            text = r.data.hex()

        output.append({
            'text': text,
            'type': r.type,
            'rect': {
                'left': r.rect.left,
                'top': r.rect.top,
                'width': r.rect.width,
                'height': r.rect.height
            }
        })

    return output
