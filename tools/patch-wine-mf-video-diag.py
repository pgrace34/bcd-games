#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-07. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""[mf-diag]: how a Media Foundation movie's frames move from the source to the screen (logging only).

Build 637 made Chrono Trigger's anime movies visible (DXMT StretchRect into the EVR mixer's plain surface), and the user
then saw the movie stutter and "occasionally go back a few frames". Build 637's log (2026-11-07 16:06) showed:
  - in the first 3 s of the movie a Wine thread-pool worker allocated about 390 frames of 1920x1080 BGRA (8 MB each,
    ~2.6 GB, iOS compressed 2 GB of it) while D3D9 LockRect, the copy of a frame into an EVR sample, ran only ~12 times;
  - then ~9 s with the process nearly idle, then a worker at ~60% of a core while the backlog drained;
  - 100 presents in the first 13.2 s (about 7.6 a second for a 29.97 fps movie).
The pipeline is: winegstreamer media source (memory samples) -> media session -> sample copier MFT (into the EVR's D3D
samples) -> EVR stream sink -> mixer (dxva2 VideoProcessBlt) -> presenter (schedules against the presentation clock).
Reading Wine's code did not show which link lets ~390 requests through, nor why frames would present out of order, so
this logs each link, rate-limited, as ERR lines (shown by the default WINEDEBUG):
  - mf/session.c: each sample request sent to a source stream; each sample queued in front of a transform because it
    refused input (the queue length at powers of two); a sample reaching a sink with no request outstanding; sink requests;
  - winegstreamer/media_source.c: each sample the source sends, per stream, with its pts;
  - mf/evr.c: a sample the EVR mixer refused (dropped) and a sample ignored because the renderer was not running;
  - evr/presenter.c: each frame shown (pts, clock, lateness, queue depth), any frame shown with a pts EARLIER than the
    one before it ("BACKWARDS"), waits of 100 ms or more, and the presenter's sample allocator running empty.
Every line carries t=<GetTickCount ms> so the rates line up with the [xp] lines. Nothing else changes.

Usage: patch-wine-mf-video-diag.py <wine source dir>. Idempotent (marker in each file); exits 1 naming the anchor that
moved, with no file changed.
"""
import pathlib
import sys

wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
MARKER = "madeira-bcd: [mf-diag]"

# Each edit: (file, name, old, new). Every old must occur exactly once in its file.
EDITS = []

# ---------------------------------------------------------------- winegstreamer/media_source.c
EDITS.append(("dlls/winegstreamer/media_source.c", "media_stream_send_sample queue",
"""    hr = IMFMediaEventQueue_QueueEventParamUnk(stream->event_queue, MEMediaSample,
            &GUID_NULL, S_OK, (IUnknown *)sample);

out:
""",
"""    hr = IMFMediaEventQueue_QueueEventParamUnk(stream->event_queue, MEMediaSample,
            &GUID_NULL, S_OK, (IUnknown *)sample);
    {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): every sample the source sends, per stream */
        static LONG sent[4];
        LONG n = InterlockedIncrement(&sent[wg_buffer->stream < 4 ? wg_buffer->stream : 3]);
        if (n <= 24 || !(n % 32))
            ERR("[mf-diag] source stream %u sample #%ld pts %d ms dur %d ms size %u hr %#lx t=%lu\\n", wg_buffer->stream, n,
                (int)(wg_buffer->pts / 10000), (int)(wg_buffer->duration / 10000), wg_buffer->size, hr, GetTickCount());
    }

out:
"""))

# ---------------------------------------------------------------- mf/session.c
EDITS.append(("dlls/mf/session.c", "source stream sample request",
"""        case MF_TOPOLOGY_SOURCESTREAM_NODE:
            if (FAILED(hr = IMFMediaStream_RequestSample(topo_node->object.source_stream, NULL)))
                WARN("Sample request failed, hr %#lx.\\n", hr);
            break;
""",
"""        case MF_TOPOLOGY_SOURCESTREAM_NODE:
            if (FAILED(hr = IMFMediaStream_RequestSample(topo_node->object.source_stream, NULL)))
                WARN("Sample request failed, hr %#lx.\\n", hr);
            {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): every request sent to a source stream */
                static LONG requests;
                LONG n = InterlockedIncrement(&requests);
                if (n <= 24 || !(n % 32))
                    ERR("[mf-diag] session request #%ld to source node %p hr %#lx t=%lu\\n", n, topo_node, hr, GetTickCount());
            }
            break;
"""))

EDITS.append(("dlls/mf/session.c", "transform input queued on MF_E_NOTACCEPTING",
"""        hr = IMFTransform_ProcessInput(transform, id, sample, 0);
        if (hr == MF_E_NOTACCEPTING)
            hr = transform_stream_push_sample(stream, sample);
""",
"""        hr = IMFTransform_ProcessInput(transform, id, sample, 0);
        if (hr == MF_E_NOTACCEPTING)
        {
            hr = transform_stream_push_sample(stream, sample);
            {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): input queued in front of a busy transform */
                unsigned int depth = list_count(&stream->samples);
                static LONG queued;
                LONG n = InterlockedIncrement(&queued);
                if (!(depth & (depth - 1)) || !(depth % 64) || n <= 8)
                    ERR("[mf-diag] transform %p input %u refused a sample: queued, depth %u (#%ld) t=%lu\\n", transform, input,
                        depth, n, GetTickCount());
            }
        }
"""))

EDITS.append(("dlls/mf/session.c", "sink node delivery",
"""        case MF_TOPOLOGY_OUTPUT_NODE:
            if (topo_node->u.sink.requests)
            {
""",
"""        case MF_TOPOLOGY_OUTPUT_NODE:
            if (sample && !topo_node->u.sink.requests)
            {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): a sample no request was outstanding for */
                static LONG unasked;
                LONG n = InterlockedIncrement(&unasked);
                if (n <= 16 || !(n % 64))
                    ERR("[mf-diag] sink node %p got a sample with no request outstanding: dropped (#%ld) t=%lu\\n", topo_node, n,
                        GetTickCount());
            }
            if (topo_node->u.sink.requests)
            {
"""))

EDITS.append(("dlls/mf/session.c", "session_request_sample tail",
"""    sink_node->u.sink.requests++;
    if (FAILED(session_request_sample_from_node(session, up_node, output)))
        sink_node->u.sink.requests--;
}
""",
"""    sink_node->u.sink.requests++;
    {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): every request a sink makes */
        static LONG sink_requests;
        LONG n = InterlockedIncrement(&sink_requests);
        if (n <= 24 || !(n % 32))
            ERR("[mf-diag] sink node %p request #%ld, outstanding %u t=%lu\\n", sink_node, n, sink_node->u.sink.requests,
                GetTickCount());
    }
    if (FAILED(session_request_sample_from_node(session, up_node, output)))
        sink_node->u.sink.requests--;
}
"""))

# ---------------------------------------------------------------- mf/evr.c
EDITS.append(("dlls/mf/evr.c", "EVR stream sink ProcessInput",
"""        if (SUCCEEDED(IMFTransform_ProcessInput(stream->parent->mixer, stream->id, sample, 0)))
        {
            while ((hr = IMFVideoPresenter_ProcessMessage(stream->parent->presenter, MFVP_MESSAGE_PROCESSINPUTNOTIFY, 0)) == MF_E_TRANSFORM_TYPE_NOT_SET)
            {
                if (FAILED(hr = IMFVideoPresenter_ProcessMessage(stream->parent->presenter, MFVP_MESSAGE_INVALIDATEMEDIATYPE, 0)))
                    break;
            }
        }
""",
"""        {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): a sample the mixer refuses is dropped here */
            HRESULT madeira_in = IMFTransform_ProcessInput(stream->parent->mixer, stream->id, sample, 0);
            if (FAILED(madeira_in))
            {
                static LONG refused;
                LONG n = InterlockedIncrement(&refused);
                if (n <= 16 || !(n % 64))
                    ERR("[mf-diag] EVR mixer refused a sample (hr %#lx), dropped #%ld pts %d ms t=%lu\\n", madeira_in, n,
                        (int)(timestamp / 10000), GetTickCount());
            }
            if (SUCCEEDED(madeira_in))
            {
                while ((hr = IMFVideoPresenter_ProcessMessage(stream->parent->presenter, MFVP_MESSAGE_PROCESSINPUTNOTIFY, 0)) == MF_E_TRANSFORM_TYPE_NOT_SET)
                {
                    if (FAILED(hr = IMFVideoPresenter_ProcessMessage(stream->parent->presenter, MFVP_MESSAGE_INVALIDATEMEDIATYPE, 0)))
                        break;
                }
            }
        }
"""))

EDITS.append(("dlls/mf/evr.c", "EVR stream sink ProcessSample tail",
"""            stream->flags |= EVR_STREAM_PREROLLED;
        }
    }

    LeaveCriticalSection(&stream->cs);

    return hr;
}
""",
"""            stream->flags |= EVR_STREAM_PREROLLED;
        }
    }
    else
    {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): not running and not prerolling: ignored */
        static LONG ignored;
        LONG n = InterlockedIncrement(&ignored);
        if (n <= 16 || !(n % 64))
            ERR("[mf-diag] EVR ignored a sample (state %d, flags %#x), #%ld pts %d ms t=%lu\\n", (int)stream->parent->state,
                (unsigned int)stream->flags, n, (int)(timestamp / 10000), GetTickCount());
    }

    LeaveCriticalSection(&stream->cs);

    return hr;
}
"""))

# ---------------------------------------------------------------- evr/presenter.c
EDITS.append(("dlls/evr/presenter.c", "presenter check_queue present/requeue",
"""        if (present)
            video_presenter_sample_present(presenter, sample);
        else
            video_presenter_sample_queue_push(presenter, sample, TRUE);
""",
"""        {   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py): what the presenter does with each frame */
            static LONG shown, backwards, waits, late;
            static LONGLONG last_pts = -1;
            if (present && presenter->clock)
            {
                LONG n = InterlockedIncrement(&shown);
                if (last_pts >= 0 && pts < last_pts && InterlockedIncrement(&backwards) <= 64)
                    ERR("[mf-diag] presenter BACKWARDS: shows pts %d ms after pts %d ms (clock %d ms, queued %u) #%ld t=%lu\\n",
                        (int)(pts / 10000), (int)(last_pts / 10000), (int)(clocktime / 10000), presenter->thread.queue.used, n,
                        GetTickCount());
                if (n <= 30 || !(n % 30))
                    ERR("[mf-diag] presenter shows #%ld pts %d ms clock %d ms ahead %d ms threshold %d ms queued %u t=%lu\\n", n,
                        (int)(pts / 10000), (int)(clocktime / 10000), (int)(delta / 10000),
                        (int)(presenter->frame_time_threshold / 10000), presenter->thread.queue.used, GetTickCount());
                else if (delta < -1000 * 10000 && InterlockedIncrement(&late) <= 32)
                    ERR("[mf-diag] presenter shows #%ld over 1 s late: pts %d ms clock %d ms t=%lu\\n", n, (int)(pts / 10000),
                        (int)(clocktime / 10000), GetTickCount());
                last_pts = pts;
            }
            else if (present)
            {
                LONG n = InterlockedIncrement(&shown);
                if (n <= 8)
                    ERR("[mf-diag] presenter shows #%ld with no clock t=%lu\\n", n, GetTickCount());
            }
            else if (wait >= 100 && InterlockedIncrement(&waits) <= 64)
                ERR("[mf-diag] presenter waits %u ms: pts %d ms clock %d ms queued %u t=%lu\\n", wait, (int)(pts / 10000),
                    (int)(clocktime / 10000), presenter->thread.queue.used, GetTickCount());
        }
        if (present)
            video_presenter_sample_present(presenter, sample);
        else
            video_presenter_sample_queue_push(presenter, sample, TRUE);
"""))

EDITS.append(("dlls/evr/presenter.c", "presenter allocator empty",
"""        if (FAILED(hr = IMFVideoSampleAllocator_AllocateSample(presenter->allocator, &sample)))
        {
            WARN("Failed to allocate a sample, hr %#lx.\\n", hr);
            break;
        }
""",
"""        if (FAILED(hr = IMFVideoSampleAllocator_AllocateSample(presenter->allocator, &sample)))
        {
            static LONG empty;   /* madeira-bcd: [mf-diag] (tools/patch-wine-mf-video-diag.py) */
            LONG n = InterlockedIncrement(&empty);
            if (n <= 16 || !(n % 64))
                ERR("[mf-diag] presenter allocator empty (hr %#lx) #%ld, queued %u t=%lu\\n", hr, n,
                    presenter->thread.queue.used, GetTickCount());
            WARN("Failed to allocate a sample, hr %#lx.\\n", hr);
            break;
        }
"""))


def main() -> int:
    texts = {}
    for rel, _, _, _ in EDITS:
        if rel not in texts:
            path = wine / rel
            if not path.is_file():
                print(f"patch-wine-mf-video-diag: {path} not found")
                return 1
            texts[rel] = path.read_text()
    done = [MARKER in t for t in texts.values()]
    if all(done):
        print("mf video diagnostics: already applied")
        return 0
    if any(done):
        print("patch-wine-mf-video-diag: partly applied (marker in some files only); restore the wine tree first")
        return 1
    for rel, name, old, _ in EDITS:
        if texts[rel].count(old) != 1:
            print(f"patch-wine-mf-video-diag: anchor moved: {name} in {rel} (found {texts[rel].count(old)} times)")
            return 1
    for rel, _, old, new in EDITS:
        texts[rel] = texts[rel].replace(old, new)
    for rel, text in texts.items():
        (wine / rel).write_text(text)
    print(f"mf video diagnostics: applied ({len(EDITS)} sites in {len(texts)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
