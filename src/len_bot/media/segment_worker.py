"""Fixed ffmpeg worker with bounded, seekable reads through the Gateway proxy."""
from __future__ import annotations

import asyncio
import json
import re
import wave
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from len_bot.execution.protocol import ExecutionRequest
from len_bot.media.segment_protocol import SegmentFrame, SegmentManifest

ROOT = Path('/workspace')


class RangeReader:
    """ffmpeg sees only two fixed local paths; the reader owns upstream URLs."""
    def __init__(self, request, proxy_url):
        self.urls = {'/video': request.video_url, '/audio': request.audio_url}
        self.limit = request.max_download_bytes
        self.received, self.requests = 0, 0
        self.error = None
        self.client = httpx.AsyncClient(proxy=proxy_url, trust_env=False, follow_redirects=False,
            timeout=20.0, headers={'Referer': 'https://www.bilibili.com/',
                'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity'})
        self.connections = set()
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0, limit=16384)
        return f'http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self.connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            lines = header.decode('ascii').split('\r\n')
            method, path, version = lines[0].split(' ')
            if method not in {'GET', 'HEAD'} or path not in self.urls or not self.urls[path]:
                raise ValueError('unsupported_media_request')
            if self.error or self.received >= self.limit or self.requests >= 128:
                raise ValueError('resource_limit')
            headers = {}
            for line in lines[1:]:
                if line.lower().startswith('range:'):
                    value = line.split(':', 1)[1].strip()
                    if 'Range' in headers or not re.fullmatch(r'bytes=[0-9]+-[0-9]*', value):
                        raise ValueError('unsupported_byte_range')
                    headers['Range'] = value
            self.requests += 1
            async with self.client.stream(method, self.urls[path], headers=headers) as response:
                if response.status_code not in {200, 206}:
                    raise ValueError(f'upstream_http_{response.status_code}')
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError('unsupported_content_encoding')
                output = [f'HTTP/1.1 {response.status_code} OK', 'Connection: close']
                for key in ('content-length', 'content-range', 'accept-ranges', 'content-type'):
                    value = response.headers.get(key)
                    if value is not None:
                        if '\r' in value or '\n' in value:
                            raise ValueError('invalid_upstream_header')
                        output.append(f'{key}: {value}')
                writer.write(('\r\n'.join(output) + '\r\n\r\n').encode('ascii'))
                await writer.drain()
                if method == 'GET':
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        self.received += len(chunk)
                        if self.received > self.limit:
                            raise ValueError('resource_limit')
                        writer.write(chunk)
                        await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            # Seeking closes the preceding byte range deliberately.
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Transport exception prose may contain a temporary signed URL.
            self.error = str(error) if isinstance(error, ValueError) else type(error).__name__
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.connections.discard(task)


async def process(argv, *, show_frames=False):
    child = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=65536)
    times = []

    async def stdout():
        value = bytearray()
        while chunk := await child.stdout.read(8192):
            value.extend(chunk)
            if len(value) > 524288:
                raise ValueError('media_metadata_too_large')
        return bytes(value)

    async def stderr():
        # Consume all diagnostic output without storing unbounded logs.
        while line := await child.stderr.readline():
            if show_frames and b'Parsed_showinfo_' in line:
                match = re.search(rb'\bpts_time:([0-9.eE+-]+)', line)
                if match and len(times) < 12:
                    times.append(float(match[1]))

    readers = [asyncio.create_task(stdout()), asyncio.create_task(stderr())]
    try:
        data, _ = await asyncio.gather(*readers)
        code = await child.wait()
        return code, data, times
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
        for task in readers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


INPUT_OPTIONS = ['-protocol_whitelist', 'http,tcp', '-f', 'mov',
                 '-probesize', '2097152', '-analyzeduration', '2000000', '-rw_timeout', '20000000']


class ProbeStream(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    codec_type: str
    width: int | None = None
    height: int | None = None


class ProbeFormat(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    duration: str = Field(pattern=r'^[0-9]+(?:\.[0-9]+)?$')


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    streams: list[ProbeStream]
    format: ProbeFormat


async def inspect_track(url, kind, request):
    code, data, _ = await process(['ffprobe', '-v', 'error', *INPUT_OPTIONS,
        '-show_entries', 'format=duration:stream=codec_type,width,height,duration', '-of', 'json', url])
    if code:
        raise ValueError('media_probe_failed')
    metadata = ProbeResult.model_validate_json(data)
    if len(metadata.streams) != 1 or metadata.streams[0].codec_type != kind:
        raise ValueError('unexpected_media_tracks')
    stream = metadata.streams[0]
    if kind == 'video' and (not stream.width or not stream.height
            or not 0 < stream.width * stream.height <= request.max_source_pixels):
        raise ValueError('source_pixel_limit')
    duration = float(metadata.format.duration)
    if duration <= 0 or request.end_ms > duration * 1000 + 100:
        raise ValueError('source_duration_mismatch')


async def extract(execution):
    request = execution.media
    control = json.loads(Path('/lenbot-control/egress.json').read_bytes())
    reader = RangeReader(request, control['proxy_url'])
    manifest = SegmentManifest(execution_id=execution.execution_id, bvid=request.bvid, cid=request.cid,
        requested_start_ms=request.start_ms, requested_end_ms=request.end_ms,
        source_duration_ms=request.source_duration_ms, status='error', downloaded_bytes=0, network_requests=0)
    start, duration = request.start_ms / 1000, (request.end_ms - request.start_ms) / 1000
    try:
        base = await reader.start()
        if request.frames:
            await inspect_track(base + '/video', 'video', request)
            interval = duration / request.frames
            select = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.9f})'"
            dimension = request.max_frame_dimension
            filters = f"{select},showinfo,scale=w='min({dimension},iw)':h='min({dimension},ih)':force_original_aspect_ratio=decrease"
            code, _, times = await process(['ffmpeg', '-nostdin', '-y', '-loglevel', 'info',
                '-threads', '1', '-filter_threads', '1', *INPUT_OPTIONS, '-ss', str(start), '-i', base + '/video',
                '-t', str(duration), '-an', '-sn', '-dn', '-map', '0:v:0', '-vf', filters,
                '-fps_mode', 'vfr', '-frames:v', str(request.frames), '-threads', '1',
                str(ROOT / 'frame_%03d.png')], show_frames=True)
            paths = sorted(ROOT.glob('frame_*.png'))
            if len(paths) > len(times):
                raise ValueError('frame_timeline_mismatch')
            for path, timestamp in zip(paths, times):
                source_time = request.start_ms + timestamp * 1000
                if not request.start_ms <= source_time < request.end_ms:
                    raise ValueError('frame_outside_selected_range')
                manifest.frames.append(SegmentFrame(path=path.name, source_time_ms=source_time))
            if code or not paths:
                raise ValueError(reader.error or 'frame_decode_failed')
        if request.audio:
            await inspect_track(base + '/audio', 'audio', request)
            code, _, _ = await process(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-threads', '1',
                *INPUT_OPTIONS, '-ss', str(start), '-i', base + '/audio', '-t', str(duration),
                '-vn', '-sn', '-dn', '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                str(ROOT / 'audio.wav')])
            if code:
                raise ValueError(reader.error or 'audio_decode_failed')
            with wave.open(str(ROOT / 'audio.wav'), 'rb') as audio:
                actual_ms = audio.getnframes() / audio.getframerate() * 1000
            if not 0 < actual_ms <= request.end_ms - request.start_ms + 1:
                raise ValueError('audio_timeline_mismatch')
            manifest.audio_path, manifest.audio_start_ms = 'audio.wav', request.start_ms
            manifest.audio_end_ms = request.start_ms + actual_ms
        if reader.error:
            raise ValueError(reader.error)
        manifest.status = 'ok'
    except Exception as error:
        manifest.status = 'partial' if manifest.frames or manifest.audio_path else 'error'
        manifest.error_code = reader.error or (str(error) if isinstance(error, ValueError) else type(error).__name__)
        manifest.detail = '仅保留清单列出的实际产物；未覆盖部分没有分析完成。'
    finally:
        await reader.close()
        manifest.downloaded_bytes, manifest.network_requests = reader.received, reader.requests
    return manifest


async def main():
    execution = ExecutionRequest.model_validate_json(Path('/lenbot-control/media.json').read_bytes())
    if execution.worker_type != 'media':
        raise ValueError('媒体入口仅接受固定片段请求')
    # This worker owns only these fixed names; previous snapshots remain in
    # the Gateway artifact store and cannot be mistaken for this extraction.
    for name in ['segment.json', 'audio.wav', *(f'frame_{i:03d}.png' for i in range(1, 13))]:
        (ROOT / name).unlink(missing_ok=True)
    manifest = await extract(execution)
    (ROOT / 'segment.json').write_text(manifest.model_dump_json(), encoding='utf-8')
    print(json.dumps({'status': manifest.status, 'manifest': 'segment.json',
        'downloaded_bytes': manifest.downloaded_bytes, 'error_code': manifest.error_code}))


if __name__ == '__main__':
    asyncio.run(main())
