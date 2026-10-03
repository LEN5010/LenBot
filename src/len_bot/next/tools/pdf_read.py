"""Bounded PDFium reads in a killable child process, never in the bot loop."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import sys

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 100
MAX_TEXT = 200_000


async def read_pdf(data: bytes, *, page: int | None = None) -> dict:
    if len(data) > MAX_PDF_BYTES:
        raise ValueError('PDF超过10MB上限')
    if page is not None and (type(page) is not int or not 1 <= page <= MAX_PAGES):
        raise ValueError('PDF页码须为1至100')
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', __name__,
        'text' if page is None else str(page), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(data), timeout=12)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError('PDF无法读取：'+stderr.decode(errors='replace')[-300:])
    return json.loads(stdout)


def _read(data, page_number):
    import pypdfium2 as pdfium
    if len(data) > MAX_PDF_BYTES:
        raise ValueError('PDF超过10MB上限')
    with pdfium.PdfDocument(data) as document:
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError('PDF须为1至100页')
        if page_number is not None:
            if not 1 <= page_number <= count:
                raise ValueError(f'PDF只有{count}页')
            page = document[page_number - 1]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(v) and 0 < v < 100_000 for v in (width, height)):
                    raise ValueError('PDF页面尺寸不合法')
                bitmap = page.render(scale=min(2.5, 2048 / max(width, height)))
                try:
                    output = io.BytesIO()
                    bitmap.to_pil().convert('RGB').save(output, format='PNG')
                finally:
                    bitmap.close()
            finally:
                page.close()
            return {'page_count': count, 'page': page_number,
                    'png_base64': base64.b64encode(output.getvalue()).decode()}
        chunks, remaining, has_text = [], MAX_TEXT, False
        for index in range(count):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_bounded()
                finally:
                    text_page.close()
            finally:
                page.close()
            chunks.append(f'\n[PDF 第 {index + 1} / {count} 页]\n'+text[:remaining])
            has_text = has_text or bool(text.strip())
            remaining -= min(len(text), remaining)
            if remaining <= 0:
                break
        return {'page_count': count, 'text': ''.join(chunks), 'truncated': remaining <= 0,
                'pages_extracted': len(chunks), 'has_text': has_text}


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        result = _read(sys.stdin.buffer.read(MAX_PDF_BYTES + 1), None if sys.argv[1] == 'text' else int(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
