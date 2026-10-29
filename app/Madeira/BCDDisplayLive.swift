// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// The display changes under the app without a restart (BCD, 2026-10-29, iPhone Duo). The Duo folds and unfolds between a
// 5.4-inch and a 7.6-inch display, two apps can share either one, and an iPad's window is resized the same way. iOS reports
// each of these as a resize of the scene and a change of its traits, never as a rotation, so the device orientation
// notification the game surface and the touch overlay listened for says nothing. BCDDisplayWatchModifier sits on the app's
// root view (MadeiraApp.swift); when that view's size changes, BCDDisplayWatch
//   1. rebuilds the Resolution choices from the display the window is on now (ResolutionChoices.screen, GuestDisplay.swift,
//      was read once, at launch: a Duo opened folded kept the outer display's sizes after unfolding);
//   2. re-frames the touch controls' window to the scene (TouchControlsHost, ContentView.swift) and lays a built-in layout
//      that is on screen unchanged out again for the new screen (ControlPresetsModel.displayChanged);
//   3. lays the game surface out again (MetalBackedView.refreshDisplayMode), as a rotation does.
// A size seen last time does nothing, so SwiftUI's repeated updates cost nothing. Everything here runs on the main thread.
// Log line: `[display] changed reason=... root=WxH screen=WxH pt WxH px default=WxH`, at most 64 times.

/// What a change of the root view's size does. Main thread.
@MainActor
enum BCDDisplayWatch {
    private static var lastSize = CGSize.zero
    private static var lines = 0

    /// The root view is `size` now (`reason`: "appear" or "resize").
    static func rootSizeChanged(_ size: CGSize, reason: String) {
        guard size.width > 0, size.height > 0, size != lastSize else { return }
        let first = lastSize == .zero
        lastSize = size
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        var choicesChanged = false
        if let screen = scene?.screen { choicesChanged = ResolutionChoices.refreshScreen(from: screen) }
        // The first size is the launch: the overlay has no window yet and no session runs.
        guard !first else { return }
        TouchControlsHost.reframe()
        MetalBackedView.refreshDisplayMode(reason: "display-" + reason)
        // The touch layout is laid out for the key window (ControlPresetsModel.currentScreen), so only while there is one:
        // without it currentScreen answers a reference phone, and a layout fitted to that would be saved.
        if scene?.windows.contains(where: { $0.isKeyWindow && $0.bounds.width > 0 }) == true {
            ControlPresetsModel.shared.displayChanged(screen: ControlPresetsModel.currentScreen())
        }
        guard lines < 64 else { return }
        lines += 1
        let s = ResolutionChoices.screen
        LogStore.shared.log(String(format: "[display] changed reason=%@ root=%.0fx%.0f screen=%.0fx%.0f pt %.0fx%.0f px%@ default=%@",
                                   reason, size.width, size.height, s.points.width, s.points.height, s.pixels.width, s.pixels.height,
                                   choicesChanged ? " (new)" : "", ResolutionChoices.defaultValue))
    }
}

/// The app's root view carries this: it reports the view's size, the whole window's, to BCDDisplayWatch.
struct BCDDisplayWatchModifier: ViewModifier {
    func body(content: Content) -> some View {
        content.background {
            GeometryReader { geo in
                Color.clear
                    .onAppear { BCDDisplayWatch.rootSizeChanged(geo.size, reason: "appear") }
                    .onChange(of: geo.size) { _, size in BCDDisplayWatch.rootSizeChanged(size, reason: "resize") }
            }
            .ignoresSafeArea()
        }
    }
}
