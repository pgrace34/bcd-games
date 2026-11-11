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
        BCDDuoLayouts.size = size   // BCD Duo fork (2026-11-10): per-shape touch layouts
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
        BCDDuoLayouts.sync(session: LibraryModel.shared.current != nil)   // BCD Duo fork: before a built-in layout is fitted again
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

/// BCD Duo fork (added 2026-11-10): one set of touch controls per screen shape, per game. A control's place is a fraction of
/// the screen, so one layout stretched over the inner screen sideways, the upright split screen and the cover screen fits
/// none of them well. While a library game runs, the controls on screen belong to the shape the screen has now; when the
/// shape changes (fold, unfold, turn) they are put away under the old shape and the new shape's own controls come back.
/// A shape with no controls of its own yet starts from the ones on screen, and edits from then on stay with that shape.
/// Kept with the game (LibraryEntry.controlShapes). Only on the foldable iPhone (BCDFoldable.active) and only while the
/// switch is on (Settings > Graphics > Foldable iPhone); off, or on any other device, nothing here runs.
enum BCDDuoLayouts {
    static let key = "bcd.duo.layouts.per-shape"
    static var on: Bool { BCDFoldable.active && UserDefaults.standard.string(forKey: BCDDuoLayouts.key) != "0" }
    static func store(_ value: Bool) { UserDefaults.standard.set(value ? "1" : "0", forKey: key) }
    /// The root view's size, from BCDDisplayWatch.
    static var size = CGSize.zero

    /// The inner display is 626 to 669 points on its short side, the cover display 466 (BCDDuoSplit.minimumWidth).
    static func shape(width: Double, height: Double, split: Bool) -> String {
        let inner = min(width, height) >= BCDDuoSplit.minimumWidth
        if width >= height { return inner ? "wide" : "cover-wide" }
        if !inner { return "cover" }
        return split ? "split" : "upright"
    }

    /// The controls on screen follow the screen's shape. `session`: a library game runs.
    static func sync(session: Bool) {
        guard on, session, size.width > 0, size.height > 0 else { return }
        let w = Double(size.width), h = Double(size.height)
        let split = BCDDuoSplit.applies(active: BCDFoldable.active, on: BCDDuoSplit.on, session: session, width: w, height: h)
        TouchControlsModel.shared.shapeChanged(to: shape(width: w, height: h, split: split))
    }
}
