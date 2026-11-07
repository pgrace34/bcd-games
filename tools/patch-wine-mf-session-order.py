#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-07. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""Media session: keep a transform's input samples in order, and one upstream request in flight per input.

Chrono Trigger plays its movies through Wine's media session: winegstreamer media source -> sample copier MFT (into the
EVR's D3D samples) -> EVR. The copier holds one input sample at a time, so the session queues the rest in front of it
(transform_stream_push_sample when ProcessInput returns MF_E_NOTACCEPTING). Build 639's [mf-diag] log (2026-11-07 19:28)
showed two faults in wine/dlls/mf/session.c:

1. Order. transform_node_push_sample() offers every arriving sample to the transform first, even while older samples
   wait in that input's queue. Whenever the copier was free, the newest frame went straight in and the waiting ones were
   fed later, so the presenter showed them after newer frames: 45 "presenter BACKWARDS" lines, from 30 ms up to 5.3 s
   back (pts 233 ms shown after 5572 ms). That is the movie "going back a few frames".
2. Over-requesting. transform_node_deliver_samples() asks the upstream node for another sample whenever the transform
   wants input and its queue is empty, without knowing that an earlier request is still on its way (the media source
   answers asynchronously). Every allocator release (session_deliver_pending_samples) and every arrival repeats it, so
   requests pile up: the source sent a 1920x1080 frame about every 5 ms while the EVR used one every 33 ms, the copier's
   queue reached 256 frames (2 GB+ of guest memory, compressed by iOS) and the presentation stalled behind it.

The fix, in session.c only:
  - struct transform_stream gains madeira_pending (requests sent upstream for this input and not yet answered) and
    madeira_replay (the sample now being delivered was popped from this input's own queue);
  - transform_node_push_sample(): an arriving sample goes behind the samples already waiting; a replayed one is offered
    to the transform first and, if refused, goes back to the head of the queue;
  - transform_node_deliver_samples(): a queued sample is replayed (flag set); otherwise upstream is asked only when no
    request for this input is in flight, and a request that was sent is counted;
  - session_deliver_sample_to_node(): an arrival at a transform input (a sample, or NULL at end of stream) answers one
    request;
  - session_flush_transform_node() (stop, seek, restart) clears both, as it clears the output requests.
A transform that needs several inputs per output (a decoder) still gets them, one request at a time.

Run before tools/patch-wine-mf-video-diag.py, which recognises the patched code. Usage: patch-wine-mf-session-order.py
<wine source dir>. Idempotent (marker "madeira-bcd: mf-session-order"); exits 1 naming the anchor that moved, the file
unchanged.
"""
import pathlib
import sys

wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
F = wine / "dlls/mf/session.c"
MARKER = "madeira-bcd: mf-session-order"

EDITS = [
("struct transform_stream",
"""struct transform_stream
{
    struct list samples;
    unsigned int requests;
    unsigned int min_buffer_size;
    BOOL draining;
};
""",
"""struct transform_stream
{
    struct list samples;
    unsigned int requests;
    unsigned int min_buffer_size;
    BOOL draining;
    /* madeira-bcd: mf-session-order (BCD Duo fork, tools/patch-wine-mf-session-order.py). Input streams only:
     * requests sent upstream for this input and not yet answered, and whether the sample being delivered now
     * was popped from this input's own queue (it keeps its place at the head instead of joining the tail). */
    unsigned int madeira_pending;
    BOOL madeira_replay;
};
"""),

("transform_stream_push_sample definition end",
"""    list_add_tail(&stream->samples, &entry->entry);
    return S_OK;
}

static HRESULT transform_stream_push_format_change(""",
"""    list_add_tail(&stream->samples, &entry->entry);
    return S_OK;
}

/* madeira-bcd: mf-session-order: a replayed sample the transform refused goes back to the head, ahead of newer ones. */
static HRESULT transform_stream_requeue_sample_head(struct transform_stream *stream, IMFSample *sample)
{
    struct list *last;
    HRESULT hr;

    if (FAILED(hr = transform_stream_push_sample(stream, sample)))
        return hr;
    last = list_tail(&stream->samples);
    list_remove(last);
    list_add_head(&stream->samples, last);
    return S_OK;
}

static HRESULT transform_stream_push_format_change("""),

("flush resets",
"""            stream = &node->u.transform.inputs[i];
            transform_stream_drop_events(stream);
            stream->draining = FALSE; /* we're about to flush, so draining can halt */
""",
"""            stream = &node->u.transform.inputs[i];
            transform_stream_drop_events(stream);
            stream->draining = FALSE; /* we're about to flush, so draining can halt */
            stream->madeira_pending = 0; /* madeira-bcd: mf-session-order: flushed requests are not answered */
            stream->madeira_replay = FALSE;
"""),

("transform_node_push_sample sample branch",
"""    if (sample)
    {
        hr = IMFTransform_ProcessInput(transform, id, sample, 0);
        if (hr == MF_E_NOTACCEPTING)
            hr = transform_stream_push_sample(stream, sample);
    }
""",
"""    if (sample)
    {
        /* madeira-bcd: mf-session-order: an arriving sample waits behind the ones already queued for this input, so the
         * transform sees them in order; a replayed (popped) one is offered first and keeps the head if refused. */
        BOOL replay = stream->madeira_replay;

        stream->madeira_replay = FALSE;
        if (!replay && !list_empty(&stream->samples))
            hr = transform_stream_push_sample(stream, sample);
        else if ((hr = IMFTransform_ProcessInput(transform, id, sample, 0)) == MF_E_NOTACCEPTING)
            hr = replay ? transform_stream_requeue_sample_head(stream, sample) : transform_stream_push_sample(stream, sample);
    }
"""),

("transform_node_deliver_samples upstream request",
"""        if (SUCCEEDED(transform_stream_pop_event(stream, &event)))
        {
            if (FAILED(hr = transform_stream_handle_event(session, stream, topo_node, input, event)))
                ERR("Failed to handle stream event, hr %#lx\\n", hr);
            IMFMediaEvent_Release(event);
        }
        else if (!(up_node = session_get_topo_node_input(session, topo_node, input, &output)))
            WARN("Failed to node %p/%lu input\\n", topo_node, input);
        else if (FAILED(hr = session_request_sample_from_node(session, up_node, output)))
            WARN("Failed to request sample from upstream node %p/%lu, hr %#lx\\n", up_node, output, hr);
    }
""",
"""        if (SUCCEEDED(transform_stream_pop_event(stream, &event)))
        {
            stream->madeira_replay = TRUE; /* madeira-bcd: mf-session-order: from this input's own queue */
            if (FAILED(hr = transform_stream_handle_event(session, stream, topo_node, input, event)))
                ERR("Failed to handle stream event, hr %#lx\\n", hr);
            stream->madeira_replay = FALSE;
            IMFMediaEvent_Release(event);
        }
        else if (stream->madeira_pending)
            TRACE("Input %lu of node %p already has %u request(s) upstream.\\n", input, topo_node, stream->madeira_pending);
        else if (!(up_node = session_get_topo_node_input(session, topo_node, input, &output)))
            WARN("Failed to node %p/%lu input\\n", topo_node, input);
        else if (FAILED(hr = session_request_sample_from_node(session, up_node, output)))
            WARN("Failed to request sample from upstream node %p/%lu, hr %#lx\\n", up_node, output, hr);
        else
            stream->madeira_pending++; /* madeira-bcd: mf-session-order: one request in flight per input */
    }
"""),

("session_deliver_sample_to_node transform case",
"""        case MF_TOPOLOGY_TRANSFORM_NODE:
            if (FAILED(hr = transform_node_push_sample(session, topo_node, input, sample)))
                WARN("Failed to push or queue sample to transform, hr %#lx\\n", hr);
""",
"""        case MF_TOPOLOGY_TRANSFORM_NODE:
            {   /* madeira-bcd: mf-session-order: an arrival from upstream (not a replay) answers one request */
                struct transform_stream *in = &topo_node->u.transform.inputs[input];
                if (!in->madeira_replay && in->madeira_pending)
                    in->madeira_pending--;
            }
            if (FAILED(hr = transform_node_push_sample(session, topo_node, input, sample)))
                WARN("Failed to push or queue sample to transform, hr %#lx\\n", hr);
"""),
]


def main() -> int:
    if not F.is_file():
        print(f"patch-wine-mf-session-order: {F} not found")
        return 1
    text = F.read_text()
    if MARKER in text:
        print("mf session order: already applied")
        return 0
    for name, old, _ in EDITS:
        if text.count(old) != 1:
            print(f"patch-wine-mf-session-order: anchor moved: {name} (found {text.count(old)} times)")
            return 1
    for _, old, new in EDITS:
        text = text.replace(old, new)
    F.write_text(text)
    print(f"mf session order: applied ({len(EDITS)} edits to dlls/mf/session.c)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
