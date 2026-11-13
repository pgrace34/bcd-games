// SPDX-License-Identifier: GPL-3.0-only
// BCD Duo fork (pgrace34/bcd-games), added 2026-11-13. Based on BCD by thebcd69; see LICENSE-BCD.md.

import GameController
import SwiftUI

/// Fast forward: while the bound controller button is held (or, in switch mode, after it is pressed), the game's clock runs
/// faster. The clock is Wine's NtQueryPerformanceCounter, scaled in build/ntdll-unix/bcd_ff_ios.c (tools/patch-wine-ff-qpc.py),
/// so a game that times itself by the clock speeds up; one that counts frames does not. Any button the controller reports
/// can be bound, the back buttons (paddles, L4/R4) included when iOS sees them. Settings > Controls > Controllers.
/// Experimental: `[fast-forward]` lines in the log say each change, the clock's lead over real time and how often the game read it.
enum BCDFastForward {
    static let buttonKey = "bcd.fast-forward.button", percentKey = "bcd.fast-forward.percent", toggleKey = "bcd.fast-forward.toggle"
    static let speeds = [150, 200, 300, 400]

    static var button: String? { UserDefaults.standard.string(forKey: buttonKey) }
    static var percent: Int {
        let v = UserDefaults.standard.integer(forKey: percentKey)
        return speeds.contains(v) ? v : 300
    }
    static var toggleMode: Bool { UserDefaults.standard.bool(forKey: toggleKey) }

    // Read and written on GamepadInput's queue only.
    private static var wasDown = false, on = false, applied = 100
    private static var settings: (button: String?, percent: Int, toggle: Bool) = (nil, 300, false)
    private static var settingsAt: TimeInterval = -10

    /// GamepadInput.sample(), on every sample while a game runs.
    static func sample(_ pads: [GCExtendedGamepad?]) {
        let now = ProcessInfo.processInfo.systemUptime
        if now - settingsAt > 1 { settings = (button, percent, toggleMode); settingsAt = now }
        guard let name = settings.button else {
            on = false
            if applied != 100 { apply(100, why: "no button") }
            return
        }
        let down = pads.contains { $0?.controller?.physicalInputProfile.buttons[name]?.isPressed == true }
        if settings.toggle { if down && !wasDown { on.toggle() } } else { on = down }
        wasDown = down
        let want = on ? settings.percent : 100
        if want != applied { apply(want, why: settings.toggle ? "switch" : "hold") }
    }

    private static func apply(_ p: Int, why: String) {
        applied = p
        let lead = bcd_ff_set_percent(UInt32(p))
        LogStore.shared.log("[fast-forward] speed=\(p)% (\(why)) clock-lead=\(lead)ms clock-reads=\(bcd_ff_reads())")
    }

    /// The pressed button of any connected controller, for "press the button to use".
    @MainActor static func pressedButton() -> String? {
        for controller in GCController.controllers() {
            let buttons = controller.physicalInputProfile.buttons
            for name in buttons.keys.sorted() where buttons[name]?.isPressed == true { return name }
        }
        return nil
    }

    /// Every button name the connected controllers report (logged when the player starts choosing, so a back button
    /// iOS does not report shows up as missing from the list).
    @MainActor static func reportButtons() {
        for controller in GCController.controllers() {
            let names = controller.physicalInputProfile.buttons.keys.sorted().joined(separator: ", ")
            LogStore.shared.log("[fast-forward] controller \"\(controller.vendorName ?? "?")\" buttons: \(names)")
        }
    }
}

/// Settings > Controls > Controllers: the fast forward button, its speed and whether it holds or switches.
struct BCDFastForwardSettingsRows: View {
    @State private var button = BCDFastForward.button
    @State private var percent = BCDFastForward.percent
    @State private var toggle = BCDFastForward.toggleMode
    @State private var listening = false
    @State private var timer: Timer?

    private func id(_ name: String) -> String { BCDSettingsID.control(.controls, name) }

    var body: some View {
        BCDSettingsActionRow(id: id("fast-forward-button"), title: "Fast forward button (experimental)",
                             note: listening ? "Press the controller button to use, for example a back button (L4 or R4). Tap here to stop."
                                : "Speeds up the game's clock while you hold it. Works in games that time themselves by the clock; "
                                + "a game that counts frames does not speed up. 32-bit games speed up smoothly, 64-bit games in steps.",
                             detail: listening ? "Press a button…" : (button ?? "None"), symbol: nil) {
            listening ? stop() : listen()
        }
        if button != nil {
            BCDSettingsValueRow(id: id("fast-forward-speed"), title: "Fast forward speed",
                                choices: BCDFastForward.speeds.map { BCDSettingsChoice(value: "\($0)", label: Self.label($0)) },
                                value: "\(percent)") { value in
                guard let v = Int(value) else { return }
                UserDefaults.standard.set(v, forKey: BCDFastForward.percentKey)
                percent = v
            }
            BCDSettingsToggleRow(id: id("fast-forward-toggle"), title: "Press to switch fast forward on and off",
                                 note: "Off: fast forward lasts while the button is held.", isOn: toggle) { value in
                UserDefaults.standard.set(value, forKey: BCDFastForward.toggleKey)
                toggle = value
            }
            BCDSettingsActionRow(id: id("fast-forward-clear"), title: "Remove the fast forward button", symbol: nil, destructive: true) {
                UserDefaults.standard.removeObject(forKey: BCDFastForward.buttonKey)
                button = nil
            }
        }
    }

    static func label(_ p: Int) -> String { p % 100 == 0 ? "\(p / 100)×" : String(format: "%.1f×", Double(p) / 100) }

    private func listen() {
        BCDFastForward.reportButtons()
        // A button still held from the tap that opened this (the controller's A) is not the answer: wait for all released.
        var armed = BCDFastForward.pressedButton() == nil
        listening = true
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                let pressed = BCDFastForward.pressedButton()
                if !armed { armed = pressed == nil; return }
                guard let pressed else { return }
                UserDefaults.standard.set(pressed, forKey: BCDFastForward.buttonKey)
                button = pressed
                LogStore.shared.log("[fast-forward] button set: \(pressed)")
                stop()
            }
        }
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
        listening = false
    }
}
