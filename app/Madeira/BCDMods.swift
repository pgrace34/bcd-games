// SPDX-License-Identifier: GPL-3.0-only
// BCD Duo fork (pgrace34/bcd-games), added 2026-11-17. Based on BCD by thebcd69; see LICENSE-BCD.md.

import SwiftUI
import UniformTypeIdentifiers

/// Mods that are not on the Steam Workshop (Nexus Mods and the like): a game page's "Mods" adds a mod from a .zip, a .7z or a single
/// file in the Files app, copies its files into the game's folder and keeps what they replaced, so switching it off or removing it
/// puts the game back as it was. Each mod lives in Application Support/BCDMods/<game folder key>/<mod id>/:
///   mod.json   the record (BCDModRecord)
///   files/     the mod's own files, as they go into the game folder (kept, so it can be switched on again or reapplied)
///   backup/    the game's files the mod replaced, while it is on
/// A Steam update or "Verify files" can put the game's originals back; the page then says so and offers Reapply.
/// A mod that ships a loader DLL Windows games pick up from their own folder (winmm.dll, version.dll, dinput8.dll...) needs Wine to
/// load that file instead of its own: BCDMods.applyOverrides, at each launch, adds "<name>=n,b" to WINEDLLOVERRIDES for the
/// switched-on mods of the game being started. `[mods]` lines in the log.
struct BCDModRecord: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    /// Paths inside the game folder, "/"-joined ("Chrono Trigger_Data/x.dat").
    var files: [String]
    /// The files of `files` the game had before the mod (now in backup/ while it is on).
    var replaced: [String] = []
    /// DLL names (no extension) Wine must load from the game folder.
    var overrides: [String] = []
    var enabled = false
    var added = Date()
}

enum BCDModsError: LocalizedError {
    case noFolder, unpack(String), empty, conflict(String), io(String)
    var errorDescription: String? {
        switch self {
        case .noFolder: return "BCD can't find this game's folder. Mods work for games installed on this iPhone (drive C)."
        case .unpack(let why): return why
        case .empty: return "That file has nothing BCD can install."
        case .conflict(let other): return "This mod changes files that \"\(other)\" also changes. Switch \"\(other)\" off first."
        case .io(let why): return why
        }
    }
}

enum BCDMods {
    /// The game's folder on this device; set by BCDLiveStore (the only part that knows where each store installs).
    @MainActor static var folderResolver: (@MainActor (BCDGame) -> (host: URL, windows: String)?)?

    /// DLLs Windows loads from a game's own folder before the system's, which mod loaders use.
    static let loaderDLLs: Set<String> = ["winmm", "version", "dinput8", "dinput", "d3d9", "d3d11", "d3d8", "dxgi", "ddraw", "dsound",
                                          "xinput1_3", "xinput1_4", "xinput9_1_0", "winhttp", "wininet", "dbghelp", "msimg32",
                                          "iphlpapi", "opengl32", "d3d10", "d3d12", "bink2w64", "binkw32", "steam_api", "steam_api64"]
    private static let junk: Set<String> = ["__MACOSX", ".DS_Store", "Thumbs.db"]
    private static let fm = FileManager.default

    static func key(_ windowsFolder: String) -> String {
        let s = windowsFolder.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "_" }
        return String(s.suffix(120))
    }
    static func store(_ windowsFolder: String) -> URL {
        fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCDMods/" + key(windowsFolder), isDirectory: true)
    }

    // MARK: records

    static func list(_ windowsFolder: String) -> [BCDModRecord] {
        let root = store(windowsFolder)
        let dirs = (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        return dirs.compactMap { dir in
            guard let data = try? Data(contentsOf: dir.appendingPathComponent("mod.json")) else { return nil }
            return try? JSONDecoder().decode(BCDModRecord.self, from: data)
        }.sorted { $0.added < $1.added }
    }
    private static func save(_ rec: BCDModRecord, _ windowsFolder: String) throws {
        let dir = store(windowsFolder).appendingPathComponent(rec.id, isDirectory: true)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        try JSONEncoder().encode(rec).write(to: dir.appendingPathComponent("mod.json"), options: .atomic)
    }
    private static func modDir(_ rec: BCDModRecord, _ windowsFolder: String) -> URL {
        store(windowsFolder).appendingPathComponent(rec.id, isDirectory: true)
    }
    private static func url(_ base: URL, _ rel: String) -> URL {
        rel.split(separator: "/").reduce(base) { $0.appendingPathComponent(String($1)) }
    }
    private static func size(_ u: URL) -> Int64? {
        (try? fm.attributesOfItem(atPath: u.path)[.size] as? NSNumber)?.int64Value
    }
    private static func isDir(_ u: URL) -> Bool {
        var d: ObjCBool = false
        return fm.fileExists(atPath: u.path, isDirectory: &d) && d.boolValue
    }

    /// Every file under `root`, "/"-joined and relative, leaving out macOS/Windows clutter.
    private static func files(under root: URL) -> [String] {
        guard let walker = fm.enumerator(at: root, includingPropertiesForKeys: [.isDirectoryKey]) else { return [] }
        var out: [String] = []
        let base = root.standardizedFileURL.path
        for case let u as URL in walker {
            if junk.contains(u.lastPathComponent) { walker.skipDescendants(); continue }
            if isDir(u) { continue }
            let p = u.standardizedFileURL.path
            guard p.hasPrefix(base + "/") else { continue }
            out.append(String(p.dropFirst(base.count + 1)))
        }
        return out.sorted()
    }

    // MARK: adding

    /// Unpacks or copies `source` into a new mod for the game, then switches it on.
    static func add(_ source: URL, host: URL, windows: String) throws -> BCDModRecord {
        let id = UUID().uuidString
        let name = source.deletingPathExtension().lastPathComponent
        let temp = fm.temporaryDirectory.appendingPathComponent("bcd-mod-" + id, isDirectory: true)
        try fm.createDirectory(at: temp, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: temp) }
        let ext = source.pathExtension.lowercased()
        if isDir(source) {
            for item in try fm.contentsOfDirectory(at: source, includingPropertiesForKeys: nil) where !junk.contains(item.lastPathComponent) {
                try fm.copyItem(at: item, to: temp.appendingPathComponent(item.lastPathComponent))
            }
        } else if ext == "rar" {
            throw BCDModsError.unpack("BCD can't unpack .rar files. Unpack it on a computer (or with an app) and add the folder or a .zip instead.")
        } else if ItchArchive.isZip(source) || ItchArchive.is7z(source) {
            do { _ = try ItchArchive.unpackAny(source, to: temp) } catch { throw BCDModsError.unpack("BCD couldn't unpack \(source.lastPathComponent): \(error.localizedDescription)") }
        } else {
            try fm.copyItem(at: source, to: temp.appendingPathComponent(source.lastPathComponent))
        }
        // A mod packed inside its own folder ("Autobattle+ v1.2/...") goes in by that folder's contents, unless the game has a
        // folder of that name (then the mod means it).
        var root = temp
        while true {
            let items = ((try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []).filter { !junk.contains($0.lastPathComponent) }
            guard items.count == 1, isDir(items[0]), !fm.fileExists(atPath: host.appendingPathComponent(items[0].lastPathComponent).path) else { break }
            root = items[0]
        }
        let list = files(under: root)
        guard !list.isEmpty else { throw BCDModsError.empty }
        var rec = BCDModRecord(id: id, name: name, files: list)
        rec.overrides = list.filter { !$0.contains("/") && $0.lowercased().hasSuffix(".dll") }
            .map { String($0.dropLast(4)).lowercased() }.filter { loaderDLLs.contains($0) }
        let dir = modDir(rec, windows)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        try fm.moveItem(at: root, to: dir.appendingPathComponent("files", isDirectory: true))
        try save(rec, windows)
        LogStore.shared.log("[mods] added \"\(name)\" files=\(list.count) loader-dlls=\(rec.overrides.joined(separator: ",")) game=\(windows)")
        return try enable(rec, host: host, windows: windows)
    }

    // MARK: on, off, reapply, remove

    /// Copies the mod's files into the game folder, keeping each game file it replaces (once). Also "Reapply": a file the game got
    /// back (a Steam update) is the new original, kept in place of the old one.
    @discardableResult
    static func enable(_ rec: BCDModRecord, host: URL, windows: String) throws -> BCDModRecord {
        var rec = rec
        if let other = list(windows).first(where: { $0.id != rec.id && $0.enabled && !Set($0.files).isDisjoint(with: rec.files) }) {
            throw BCDModsError.conflict(other.name)
        }
        let dir = modDir(rec, windows)
        let src = dir.appendingPathComponent("files"), backup = dir.appendingPathComponent("backup")
        var replaced = Set(rec.replaced)
        do {
            for rel in rec.files {
                let from = url(src, rel), to = url(host, rel)
                if fm.fileExists(atPath: to.path) {
                    if rec.enabled, size(to) == size(from) { continue }   // already the mod's (same size): left alone
                    let keep = url(backup, rel)
                    try fm.createDirectory(at: keep.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try? fm.removeItem(at: keep)
                    try fm.moveItem(at: to, to: keep)
                    replaced.insert(rel)
                } else {
                    try fm.createDirectory(at: to.deletingLastPathComponent(), withIntermediateDirectories: true)
                }
                try fm.copyItem(at: from, to: to)
            }
        } catch {
            throw BCDModsError.io("BCD couldn't copy the mod into the game's folder: \(error.localizedDescription)")
        }
        rec.replaced = replaced.sorted()
        rec.enabled = true
        try save(rec, windows)
        LogStore.shared.log("[mods] on \"\(rec.name)\" replaced=\(rec.replaced.count)")
        return rec
    }

    /// Takes the mod's files out of the game folder and puts back what they replaced.
    @discardableResult
    static func disable(_ rec: BCDModRecord, host: URL, windows: String) throws -> BCDModRecord {
        var rec = rec
        let backup = modDir(rec, windows).appendingPathComponent("backup")
        do {
            for rel in rec.files {
                let to = url(host, rel)
                try? fm.removeItem(at: to)
                let keep = url(backup, rel)
                if fm.fileExists(atPath: keep.path) { try fm.moveItem(at: keep, to: to) }
            }
        } catch {
            throw BCDModsError.io("BCD couldn't put the game's own files back: \(error.localizedDescription)")
        }
        rec.replaced = []
        rec.enabled = false
        try save(rec, windows)
        LogStore.shared.log("[mods] off \"\(rec.name)\"")
        return rec
    }

    static func remove(_ rec: BCDModRecord, host: URL, windows: String) throws {
        if rec.enabled { try disable(rec, host: host, windows: windows) }
        try? fm.removeItem(at: modDir(rec, windows))
        LogStore.shared.log("[mods] removed \"\(rec.name)\"")
    }

    /// A switched-on mod some of whose files are no longer the mod's (a Steam update or Verify files put the game's back).
    static func needsReapply(_ rec: BCDModRecord, host: URL, windows: String) -> Bool {
        guard rec.enabled else { return false }
        let src = modDir(rec, windows).appendingPathComponent("files")
        return rec.files.contains { size(url(host, $0)) != size(url(src, $0)) }
    }

    // MARK: launch

    /// Before a session: the switched-on mods' loader DLLs of the game in `windowsFolder` go into WINEDLLOVERRIDES as "name=n,b"
    /// (added to what is there already). Called from LibraryEntry.configureLaunch.
    static func applyOverrides(gameFolder windowsFolder: String?) {
        guard let windowsFolder else { return }
        let names = Set(list(windowsFolder).filter(\.enabled).flatMap(\.overrides))
        guard !names.isEmpty else { return }
        let ours = names.sorted().map { "\($0)=n,b" }
        let old = ProcessInfo.processInfo.environment["WINEDLLOVERRIDES"].map { $0.split(separator: ";").map(String.init) } ?? []
        let kept = old.filter { entry in !names.contains(String(entry.split(separator: "=").first ?? "").lowercased()) }
        let value = (kept + ours).joined(separator: ";")
        setenv("WINEDLLOVERRIDES", value, 1)
        LogStore.shared.log("[mods] WINEDLLOVERRIDES=\(value)")
    }
}

// MARK: - The page

/// A game's Mods page (game page › Mods): the mods added, each with a switch, Reapply when the game's own files came back, and
/// Remove; "Add a mod" opens Files for a .zip, .7z or single file.
struct BCDModsView: View {
    let game: BCDGame
    @Environment(\.dismiss) private var dismiss
    @State private var place: (host: URL, windows: String)?
    @State private var mods: [BCDModRecord] = []
    @State private var stale: Set<String> = []
    @State private var picking = false
    @State private var busy = false
    @State private var error: String?
    @State private var removing: BCDModRecord?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if place == nil {
                        Text(BCDModsError.noFolder.errorDescription ?? "").foregroundStyle(.secondary)
                    } else if mods.isEmpty {
                        Text("No mods yet. Download a mod (from Nexus Mods, say) to Files, then tap Add a mod. "
                             + "BCD copies its files into the game's folder and keeps the game's own, so you can switch it off any time.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(mods) { mod in row(mod) }
                } footer: {
                    if place != nil {
                        Text("Mods that need an installer or a mod manager won't work here. "
                             + "A Steam update can put the game's own files back; BCD then offers Reapply.")
                    }
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Mods: \(game.title)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                ToolbarItem(placement: .primaryAction) {
                    Button { picking = true } label: { Label("Add a mod", systemImage: "plus") }
                        .disabled(place == nil || busy)
                }
            }
            .overlay { if busy { ProgressView().controlSize(.large) } }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.zip, .item, .folder]) { result in
                guard case .success(let url) = result, let place else { return }
                work({ _ = try BCDMods.add(url, host: place.host, windows: place.windows) }, scoped: url)
            }
            .confirmationDialog("Remove this mod?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                                titleVisibility: .visible, presenting: removing) { mod in
                Button("Remove \"\(mod.name)\"", role: .destructive) {
                    guard let place else { return }
                    work { try BCDMods.remove(mod, host: place.host, windows: place.windows) }
                }
            } message: { _ in Text("Its files leave the game's folder and the game's own come back. Add it again from Files to use it later.") }
        }
        .onAppear {
            place = BCDMods.folderResolver?(game)
            reload()
        }
    }

    private func row(_ mod: BCDModRecord) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: Binding(get: { mod.enabled }, set: { on in
                guard let place else { return }
                work {
                    if on { try BCDMods.enable(mod, host: place.host, windows: place.windows) }
                    else { try BCDMods.disable(mod, host: place.host, windows: place.windows) }
                }
            })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(mod.name).font(.headline)
                    Text("\(mod.files.count) file\(mod.files.count == 1 ? "" : "s")"
                         + (mod.overrides.isEmpty ? "" : " · loads \(mod.overrides.map { $0 + ".dll" }.joined(separator: ", "))"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .disabled(busy)
            HStack {
                if stale.contains(mod.id) {
                    Button("Reapply") {
                        guard let place else { return }
                        work { try BCDMods.enable(mod, host: place.host, windows: place.windows) }
                    }
                    .buttonStyle(.borderedProminent)
                    Text("The game's own files came back (an update?).").font(.caption).foregroundStyle(.orange)
                }
                Spacer()
                Button("Remove", role: .destructive) { removing = mod }.buttonStyle(.borderless).font(.caption)
            }
        }
        .padding(.vertical, 4)
    }

    private func reload() {
        guard let place else { mods = []; return }
        mods = BCDMods.list(place.windows)
        stale = Set(mods.filter { BCDMods.needsReapply($0, host: place.host, windows: place.windows) }.map(\.id))
    }

    /// Runs file work off the main thread, then reloads; a picked file is opened through its security scope.
    private func work(_ job: @escaping () throws -> Void, scoped: URL? = nil) {
        busy = true
        error = nil
        Task.detached {
            let scope = scoped?.startAccessingSecurityScopedResource() ?? false
            defer { if scope { scoped?.stopAccessingSecurityScopedResource() } }
            var message: String?
            do { try job() } catch { message = error.localizedDescription; LogStore.shared.log("[mods] error: \(message ?? "")") }
            await MainActor.run {
                error = message
                busy = false
                reload()
            }
        }
    }
}
