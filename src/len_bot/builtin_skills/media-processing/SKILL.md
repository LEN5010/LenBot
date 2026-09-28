---
name: media-processing
description: Convert, trim, combine, or inspect audio, video, and image media using the task image's actual FFmpeg capabilities.
---

# Media processing

Use `ffmpeg` for concrete media transformations. Check the source streams and installed encoders/filters first (`ffmpeg -i`, and `ffprobe` if present); do not assume a codec, GPU path, or extra Python package is installed.

- Decide which source streams to keep, their time range, target format, dimensions, frame rate, and audio treatment from the request. Preserve an untouched source and use a distinct output path under `/workspace/out`.
- Run the chosen command with explicit maps and codec settings. Check its exit status and output, then inspect duration, dimensions, audio channels, and representative frames or short playback segments as appropriate. A file's existence alone does not prove it contains the requested cut.
- For subtitles or text overlays, the image includes Noto CJK fonts; verify actual font selection and readable placement on sample frames. If a required encoder/filter is absent, report the exact limitation rather than silently substituting another format.
- Register the checked output via `deliver_file(path, name, note)`. Registration is not a QQ upload or playback confirmation.
