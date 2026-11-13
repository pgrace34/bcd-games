/* SPDX-License-Identifier: GPL-3.0-or-later
 * BCD Duo fork (pgrace34/bcd-games), added 2026-11-13. Based on BCD by thebcd69; see LICENSE-BCD.md.
 *
 * Fast forward: the game's clock runs faster while the player holds the bound button.
 *
 * Every Windows clock a game paces itself with reaches the unix side through NtQueryPerformanceCounter
 * (tools/patch-wine-ff-qpc.py): QueryPerformanceCounter and timeGetTime directly, GetTickCount and GetTickCount64
 * through kernelbase's madeira_interrupt_time (ml1232). This file turns the real counter (mach_continuous_time in
 * 100 ns units, as monotonic_counter() reads it) into the game's counter:
 *
 *     game = base_game + (real - base_real) * percent / 100
 *
 * re-based whenever the percent changes, so the game's clock never jumps and never goes back; a read never
 * returns less than the last value returned (threads that read the real counter just before a re-base). While
 * nothing has ever changed the percent, a read is the real counter and costs one relaxed load.
 *
 * Limits: waits, sleeps and timers keep real time (a frame limiter that sleeps still works, sleeping less in game
 * time). A 64-bit game's ntdll reads QueryPerformanceCounter in user mode (ml1117) and only re-syncs with this
 * counter about once a second, so there the speed-up arrives in steps; 32-bit games (wow64) read it here every time.
 * Audio plays at its real rate. The app sets the percent (BCDFastForward.swift); [fast-forward] lines log it.
 */
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

static pthread_mutex_t ff_lock = PTHREAD_MUTEX_INITIALIZER;
static int ff_touched;                 /* the percent has changed at least once this run */
static uint32_t ff_percent = 100;
static uint64_t ff_base_real, ff_base_game, ff_last;
static volatile uint64_t ff_reads;     /* reads through the scaled path, for the app's log */

static uint64_t ff_real_now(void)
{
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info( &tb );
    return mach_continuous_time() * tb.numer / tb.denom / 100;
}

static uint64_t ff_game_locked( uint64_t real )
{
    uint64_t game = real >= ff_base_real
        ? ff_base_game + (real - ff_base_real) * ff_percent / 100
        : ff_base_game;
    if (game < ff_last) game = ff_last;
    ff_last = game;
    return game;
}

__attribute__((visibility("default"))) uint64_t bcd_ff_time( uint64_t real )
{
    uint64_t game;
    if (!__atomic_load_n( &ff_touched, __ATOMIC_ACQUIRE )) return real;
    pthread_mutex_lock( &ff_lock );
    game = ff_game_locked( real );
    pthread_mutex_unlock( &ff_lock );
    __atomic_fetch_add( &ff_reads, 1, __ATOMIC_RELAXED );
    return game;
}

/* 100 = real time; clamped to 100...800. Returns the game clock's lead over real time, in milliseconds. */
__attribute__((visibility("default"))) int64_t bcd_ff_set_percent( uint32_t percent )
{
    uint64_t real;
    int64_t lead;
    if (percent < 100) percent = 100;
    if (percent > 800) percent = 800;
    pthread_mutex_lock( &ff_lock );
    real = ff_real_now();
    if (!ff_touched) { ff_base_real = ff_base_game = ff_last = real; }
    else { ff_base_game = ff_game_locked( real ); ff_base_real = real; }
    ff_percent = percent;
    lead = (int64_t)(ff_base_game - real) / 10000;
    pthread_mutex_unlock( &ff_lock );
    __atomic_store_n( &ff_touched, 1, __ATOMIC_RELEASE );
    return lead;
}

__attribute__((visibility("default"))) uint64_t bcd_ff_reads(void)
{
    return __atomic_load_n( &ff_reads, __ATOMIC_RELAXED );
}
