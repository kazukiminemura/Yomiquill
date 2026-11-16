from contextlib import closing
from threading import Lock

from pypdfium2 import PdfDocument, PdfiumError
from pypdfium2.raw import FPDFBitmap_BGRx
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


PDFIUM_LOCK = Lock()


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("PDF のページ画像を作成できませんでした。")
    return bytes(buffer.data())


def rendered_pages(data, page_numbers):
    """Render selected 1-based PDF pages as PNG for local OCR."""
    try:
        # PDFium calls must be serialized even when the jobs use different files.
        with PDFIUM_LOCK:
            pdf = PdfDocument(data)
        try:
            for number in page_numbers:
                with PDFIUM_LOCK, closing(pdf[number - 1]) as page:
                    scale = min(4, 3200 / max(page.get_size()))
                    with closing(page.render(
                        scale=scale, rev_byteorder=True, force_bitmap_format=FPDFBitmap_BGRx
                    )) as bitmap:
                        image = QImage(
                            bitmap.buffer, bitmap.width, bitmap.height, bitmap.stride,
                            QImage.Format.Format_RGBX8888,
                        )
                        png = png_bytes(image)
                yield number, png
        finally:
            with PDFIUM_LOCK:
                pdf.close()
    except PdfiumError as error:
        raise ValueError("PDF を開けませんでした。破損やパスワード保護を確認してください。") from error


def dark_heading_images(data):
    """Recover reversed text in broad title bands that whole-page OCR can miss."""
    import numpy as np

    image = QImage.fromData(data).convertToFormat(QImage.Format.Format_Grayscale8)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    if not width or not height:
        return
    pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, stride)
    coverage = (pixels[:, :width] < 100).mean(axis=1)
    boundaries = np.flatnonzero(np.diff(np.r_[False, coverage > 0.39, False]))
    bands = [
        (start, end) for start, end in zip(boundaries[::2], boundaries[1::2])
        if max(12, height // 100) <= end - start <= height // 5
    ]
    for start, end in bands[:6]:
        margin = max(2, (end - start) // 20)
        block = pixels[max(0, start - margin):min(height, end + margin)]
        reversed_text = np.where(block < 140, 255, 0).astype(np.uint8)
        crop = QImage(reversed_text, width, len(block), stride, QImage.Format.Format_Grayscale8)
        yield png_bytes(crop)
