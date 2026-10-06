"""Bounded ISO-BMFF metadata checks for short H.264 social videos."""

import struct

from rest_framework.exceptions import ValidationError


def boxes(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError("Truncated MP4 box")
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            if len(data) - offset < 16:
                raise ValueError("Truncated extended box")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError("Invalid MP4 box size")
        yield kind, data[offset + header : offset + size]
        offset += size


def validate_video(upload):
    try:
        if not 0 < upload.size <= 8 * 1024 * 1024:
            raise ValueError("Video must be at most 8 MiB")
        top = dict(boxes(upload.read()))
        if not {b"ftyp", b"moov", b"mdat"}.issubset(top) or not top[b"mdat"]:
            raise ValueError("Use an MP4 video")
        ftyp = top[b"ftyp"]
        if len(ftyp) < 8 or ftyp[:4] not in {
            b"isom",
            b"iso2",
            b"mp41",
            b"mp42",
            b"avc1",
        }:
            raise ValueError("Unsupported MP4 brand")
        movie = list(boxes(top[b"moov"]))
        mvhd = next(payload for kind, payload in movie if kind == b"mvhd")
        if mvhd[0] != 0:
            raise ValueError("Unsupported movie metadata")
        scale, duration = struct.unpack_from(">II", mvhd, 12)
        if not scale or not 1 <= duration / scale <= 60:
            raise ValueError("Video must last 1–60 seconds")
        videos = 0
        for kind, payload in movie:
            if kind != b"trak":
                continue
            track = dict(boxes(payload))
            media = dict(boxes(track[b"mdia"]))
            if media[b"hdlr"][8:12] != b"vide":
                continue
            videos += 1
            width, height = struct.unpack(">II", track[b"tkhd"][-8:])
            if (width >> 16, height >> 16) not in {(720, 1280), (1080, 1920)}:
                raise ValueError("Use a 720×1280 or 1080×1920 vertical video")
            sample = dict(boxes(dict(boxes(media[b"minf"]))[b"stbl"]))[b"stsd"]
            if struct.unpack_from(">I", sample, 4)[0] != 1 or sample[12:16] != b"avc1":
                raise ValueError("Use H.264 video")
        if videos != 1:
            raise ValueError("Use exactly one video track")
        upload.detected_extension = ".mp4"
        return upload
    except (ValueError, KeyError, IndexError, StopIteration, struct.error) as exc:
        raise ValidationError(
            {"video": "Use a valid H.264 vertical MP4, 1–60 seconds and at most 8 MiB."}
        ) from exc
    finally:
        upload.seek(0)
