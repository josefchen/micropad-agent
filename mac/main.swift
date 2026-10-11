// Micropad menu bar + wheel switcher HUD.
// Plain AppKit, no SwiftUI/WebKit: one status item, one lazily built panel,
// fed by the daemon's /events stream (server-sent events, no polling).
import AppKit

let daemon = URL(string: "http://127.0.0.1:48801")!

struct Session: Decodable {
    let led: Int?
    let sid: String?
    let family: String?
    let state: String?
    let ts: Double?
    let title: String?
    let workspace: String?
    let tokens: Int?
    let cached: Int?
}

struct Switcher: Decodable {
    let sid: String?
    let order: [String]?
    let mode: String?
    let holdMs: Int?
}

func decodeSnapshot(_ data: Data) throws -> Snapshot {
    let d = JSONDecoder()
    d.keyDecodingStrategy = .convertFromSnakeCase
    return try d.decode(Snapshot.self, from: data)
}

func compact(_ n: Int) -> String {
    let v = Double(n)
    if v >= 1e9 { return String(format: "%.1fB", v / 1e9) }
    if v >= 1e6 { return String(format: "%.1fM", v / 1e6) }
    if v >= 1e3 { return String(format: "%.0fk", v / 1e3) }
    return "\(n)"
}

struct Snapshot: Decodable {
    let pad: Bool
    let keys: [Session]
    let others: [Session]
    let switcher: Switcher?
    let warnings: [String]
}

extension Session {
    var app: String {
        switch family {
        case "codex": return "Codex"
        case "cursor": return "Cursor"
        default: return "Claude"
        }
    }

    var familyColour: NSColor {
        switch family {
        case "codex": return NSColor(srgbRed: 0.36, green: 0.60, blue: 1.0, alpha: 1)
        case "cursor": return NSColor(srgbRed: 0.70, green: 0.48, blue: 1.0, alpha: 1)
        default: return NSColor(srgbRed: 1.0, green: 0.66, blue: 0.24, alpha: 1)
        }
    }

    var stateColour: NSColor {
        switch state {
        case "waiting": return NSColor(srgbRed: 1.0, green: 0.58, blue: 0.20, alpha: 1)
        case "done": return NSColor(srgbRed: 0.34, green: 0.86, blue: 0.48, alpha: 1)
        case "error": return NSColor(srgbRed: 1.0, green: 0.36, blue: 0.36, alpha: 1)
        default: return NSColor(white: 1, alpha: 0.65)
        }
    }

    /// Menu bar dot: green/red once finished, otherwise the app colour.
    var colour: NSColor {
        state == "done" || state == "error" ? stateColour : familyColour
    }

    var appIcon: NSImage? {
        let ids: [String]
        switch family {
        case "codex": ids = ["com.openai.codex", "com.openai.chat"]
        case "cursor": ids = ["com.todesktop.230313mzl4w4u92"]
        default: ids = ["com.anthropic.claudefordesktop"]
        }
        for id in ids {
            if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) {
                return NSWorkspace.shared.icon(forFile: url.path)
            }
        }
        return nil
    }

    var stateLabel: String {
        switch state {
        case "waiting": return "Needs you"
        case "working": return "Working"
        case "done": return "Done"
        case "error": return "Error"
        default: return state ?? ""
        }
    }

    var age: String {
        guard let ts else { return "" }
        let s = max(0, Int(Date().timeIntervalSince1970 - ts))
        if s < 60 { return "\(s)s" }
        if s < 3600 { return "\(s / 60)m" }
        return "\(s / 3600)h"
    }

    var tokensBurnt: String? {
        guard let tokens, tokens > 0 else { return nil }
        return "\(compact(tokens)) tokens"
    }

    /// Cursor keeps no token counts on disk (every message logs 0 / 0).
    var tokenDetail: String {
        guard let tokens, tokens > 0 else { return family == "cursor" ? "Cursor doesn't log tokens" : "No tokens yet" }
        let pct = Int((Double(cached ?? 0) / Double(tokens) * 100).rounded())
        return pct > 0 ? "\(pct)% from cache" : "burnt"
    }

    var displayTitle: String {
        if let t = title, !t.isEmpty { return t }
        if let w = workspace, !w.isEmpty { return (w as NSString).lastPathComponent }
        return app
    }
}

func post(_ path: String, _ body: [String: Any]) {
    var req = URLRequest(url: daemon.appendingPathComponent(path))
    req.httpMethod = "POST"
    req.setValue("1", forHTTPHeaderField: "X-Micropad")
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try? JSONSerialization.data(withJSONObject: body)
    URLSession.shared.dataTask(with: req).resume()
}

// MARK: - event stream

final class EventStream: NSObject, URLSessionDataDelegate {
    private var session: URLSession!
    private var buffer = Data()
    private let onSnapshot: (Snapshot?) -> Void

    init(onSnapshot: @escaping (Snapshot?) -> Void) {
        self.onSnapshot = onSnapshot
        super.init()
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 60
        cfg.timeoutIntervalForResource = .infinity
        cfg.urlCache = nil
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: .main)
        connect()
    }

    private func connect() {
        buffer.removeAll(keepingCapacity: true)
        var req = URLRequest(url: daemon.appendingPathComponent("events"))
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        session.dataTask(with: req).resume()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        let sep = Data("\n\n".utf8)
        while let r = buffer.range(of: sep) {
            let event = buffer.subdata(in: buffer.startIndex..<r.lowerBound)
            buffer.removeSubrange(buffer.startIndex..<r.upperBound)
            guard let text = String(data: event, encoding: .utf8) else { continue }
            for line in text.split(separator: "\n") where line.hasPrefix("data: ") {
                do {
                    onSnapshot(try decodeSnapshot(Data(line.dropFirst(6).utf8)))
                } catch {
                    FileHandle.standardError.write(Data("micropad: bad snapshot: \(error)\n".utf8))
                }
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        onSnapshot(nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.connect() }
    }
}

// MARK: - switcher HUD
// Dark glass: the blur is composited by the WindowServer, so it costs the app
// nothing; a dark tint keeps text readable over any wallpaper and in light mode.
// `--render` has no window to blur, so it draws the tint opaque.

final class Shape: NSView {
    var fill: NSColor = .clear
    var stroke: NSColor = .clear
    var lineWidth: CGFloat = 0
    var radius: CGFloat = 0
    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        let inset = lineWidth / 2
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: inset, dy: inset), xRadius: radius, yRadius: radius)
        fill.setFill()
        path.fill()
        if lineWidth > 0 {
            stroke.setStroke()
            path.lineWidth = lineWidth
            path.stroke()
        }
    }
}

/// The pad seen from above: LED index per key, plus the two encoders.
enum PadCell { case key(Int), wheel, knob, blank }

let padLayout: [[PadCell]] = [
    [.wheel, .key(10), .key(11), .knob],
    [.key(9), .key(8), .key(7), .key(6)],
    [.key(2), .key(3), .key(4), .key(5)],
    [.blank, .key(1), .key(0), .blank],
]

enum HUD {
    static let pad: CGFloat = 22
    static let header: CGFloat = 24
    static let footer: CGFloat = 34
    static let tile = NSSize(width: 158, height: 150)
    static let gap: CGFloat = 10
    static let recentWidth: CGFloat = 260
    static let recentRow: CGFloat = 58
}

func label(_ text: String, size: CGFloat, weight: NSFont.Weight, colour: NSColor, lines: Int = 1,
           align: NSTextAlignment = .center) -> NSTextField {
    let f = lines > 1 ? NSTextField(wrappingLabelWithString: text) : NSTextField(labelWithString: text)
    f.font = .systemFont(ofSize: size, weight: weight)
    f.textColor = colour
    f.alignment = align
    f.maximumNumberOfLines = lines
    f.lineBreakMode = lines > 1 ? .byWordWrapping : .byTruncatingTail
    f.cell?.truncatesLastVisibleLine = true
    return f
}

private func add(_ parent: NSView, _ child: NSView, _ frame: NSRect) {
    child.frame = frame
    parent.addSubview(child)
}

private func shape(fill: NSColor, stroke: NSColor = .clear, width: CGFloat = 0, radius: CGFloat) -> Shape {
    let s = Shape()
    s.fill = fill
    s.stroke = stroke
    s.lineWidth = width
    s.radius = radius
    return s
}

private func panelRoot(_ size: NSSize, glass: Bool) -> Shape {
    let root = shape(fill: NSColor(white: 0.06, alpha: glass ? 0.55 : 0.96),
                     stroke: NSColor(white: 1, alpha: 0.10), width: 1, radius: 26)
    root.frame = NSRect(origin: .zero, size: size)
    root.appearance = NSAppearance(named: .darkAqua)
    return root
}

final class IconView: NSView {
    var image: NSImage?
    override func draw(_ dirtyRect: NSRect) {
        image?.draw(in: bounds, from: .zero, operation: .sourceOver, fraction: 1)
    }
}

private func icon(_ s: Session) -> NSView {
    let v = IconView()
    v.image = s.appIcon
    return v
}

private let dim = NSColor(white: 1, alpha: 0.4)

/// A key being held: how far through the hold it already is, and how long remains.
struct Hold {
    let done: Double
    let remaining: TimeInterval
}

// MARK: wheel view: sessions on their keys, in the pad's shape, recent ones alongside

func padView(keys: [Session], recent: [Session], selected: String?, glass: Bool) -> NSView {
    let t = HUD.tile, g = HUD.gap
    let gridW = 4 * t.width + 3 * g, gridH = 4 * t.height + 3 * g
    let recentW = recent.isEmpty ? 0 : 2 * g + HUD.recentWidth
    let size = NSSize(width: HUD.pad * 2 + gridW + recentW,
                      height: HUD.pad * 2 + HUD.header + gridH + HUD.footer)
    let root = panelRoot(size, glass: glass)
    let top = HUD.pad + HUD.header

    add(root, label("ON THE PAD", size: 11, weight: .bold, colour: dim, align: .left),
        NSRect(x: HUD.pad + 4, y: HUD.pad, width: 200, height: 16))
    let byLed = Dictionary(keys.compactMap { s in s.led.map { ($0, s) } }, uniquingKeysWith: { a, _ in a })
    for (r, row) in padLayout.enumerated() {
        for (c, cell) in row.enumerated() {
            let frame = NSRect(x: HUD.pad + CGFloat(c) * (t.width + g), y: top + CGFloat(r) * (t.height + g),
                               width: t.width, height: t.height)
            switch cell {
            case .key(let led):
                if let s = byLed[led], s.sid != nil {
                    add(root, tile(s, selected: s.sid == selected), frame)
                } else {
                    add(root, shape(fill: NSColor(white: 1, alpha: 0.03), stroke: NSColor(white: 1, alpha: 0.07),
                                    width: 1, radius: 16), frame)
                }
            case .wheel: add(root, encoder("Scroll to choose", active: true), frame)
            case .knob: add(root, encoder("Volume", active: false), frame)
            case .blank: break
            }
        }
    }

    if !recent.isEmpty {
        let x = HUD.pad + gridW + 2 * g
        add(root, label("RECENT", size: 11, weight: .bold, colour: dim, align: .left),
            NSRect(x: x + 4, y: HUD.pad, width: 200, height: 16))
        for (i, s) in recent.enumerated() {
            add(root, recentRow(s, selected: s.sid == selected),
                NSRect(x: x, y: top + CGFloat(i) * (HUD.recentRow + 8), width: HUD.recentWidth, height: HUD.recentRow))
        }
    }

    add(root, label("Turn the wheel to choose  ·  stop to open", size: 12, weight: .medium, colour: dim),
        NSRect(x: 0, y: size.height - HUD.pad - 18, width: size.width, height: 18))
    return root
}

private func tile(_ s: Session, selected: Bool) -> NSView {
    let w = HUD.tile.width
    let v = shape(fill: selected ? s.familyColour.withAlphaComponent(0.22) : NSColor(white: 1, alpha: 0.06),
                  stroke: selected ? s.familyColour : s.familyColour.withAlphaComponent(0.35),
                  width: selected ? 3 : 1, radius: 16)
    v.frame.size = HUD.tile
    add(v, icon(s), NSRect(x: (w - 30) / 2, y: 12, width: 30, height: 30))
    add(v, label(s.app.uppercased(), size: 11, weight: .bold, colour: s.familyColour), NSRect(x: 6, y: 45, width: w - 12, height: 14))
    add(v, label(s.displayTitle, size: 13, weight: .semibold, colour: .white, lines: 2), NSRect(x: 8, y: 61, width: w - 16, height: 34))
    let tokens = label(s.tokensBurnt ?? "—", size: 14, weight: .bold, colour: NSColor(white: 1, alpha: s.tokensBurnt == nil ? 0.3 : 0.92))
    tokens.font = .monospacedDigitSystemFont(ofSize: 14, weight: .bold)
    add(v, tokens, NSRect(x: 6, y: 100, width: w - 12, height: 18))
    add(v, label("● \(s.stateLabel) · \(s.age)", size: 11, weight: .semibold, colour: s.stateColour),
        NSRect(x: 6, y: 124, width: w - 12, height: 15))
    return v
}

private func encoder(_ caption: String, active: Bool) -> NSView {
    let v = NSView(frame: NSRect(origin: .zero, size: HUD.tile))
    let d: CGFloat = 92
    add(v, shape(fill: NSColor(white: 1, alpha: active ? 0.10 : 0.04),
                 stroke: NSColor(white: 1, alpha: active ? 0.45 : 0.10), width: active ? 2 : 1, radius: d / 2),
        NSRect(x: (HUD.tile.width - d) / 2, y: 14, width: d, height: d))
    add(v, label(caption, size: 11, weight: .medium, colour: NSColor(white: 1, alpha: active ? 0.6 : 0.3)),
        NSRect(x: 0, y: 118, width: HUD.tile.width, height: 15))
    return v
}

private func recentRow(_ s: Session, selected: Bool) -> NSView {
    let w = HUD.recentWidth, h = HUD.recentRow
    let v = shape(fill: selected ? s.familyColour.withAlphaComponent(0.22) : NSColor(white: 1, alpha: 0.04),
                  stroke: s.familyColour, width: selected ? 3 : 0, radius: 14)
    v.frame.size = NSSize(width: w, height: h)
    v.alphaValue = selected ? 1 : 0.7
    add(v, icon(s), NSRect(x: 12, y: (h - 30) / 2, width: 30, height: 30))
    add(v, label(s.displayTitle, size: 13, weight: .semibold, colour: .white, align: .left),
        NSRect(x: 52, y: 11, width: w - 64, height: 17))
    let meta = "\(s.tokensBurnt ?? s.app)  ·  \(s.stateLabel) · \(s.age)"
    add(v, label(meta, size: 11, weight: .medium, colour: NSColor(white: 1, alpha: 0.55), align: .left),
        NSRect(x: 52, y: 31, width: w - 64, height: 15))
    return v
}

// MARK: hold view: just the held key's session, and where that key is on the pad

func holdView(_ s: Session, hold: Hold?, glass: Bool) -> NSView {
    let cell: CGFloat = 26, cg: CGFloat = 5
    let map = 4 * cell + 3 * cg
    let card = NSSize(width: 320, height: 224)
    let size = NSSize(width: HUD.pad * 2 + map + 28 + card.width, height: HUD.pad * 2 + card.height + HUD.footer)
    let root = panelRoot(size, glass: glass)

    let mapTop = HUD.pad + (card.height - map) / 2
    for (r, row) in padLayout.enumerated() {
        for (c, k) in row.enumerated() {
            let f = NSRect(x: HUD.pad + CGFloat(c) * (cell + cg), y: mapTop + CGFloat(r) * (cell + cg), width: cell, height: cell)
            switch k {
            case .key(let led):
                let mine = led == s.led
                add(root, shape(fill: mine ? s.familyColour : NSColor(white: 1, alpha: 0.08), radius: 6), f)
            case .wheel, .knob:
                add(root, shape(fill: .clear, stroke: NSColor(white: 1, alpha: 0.15), width: 1.5, radius: cell / 2), f)
            case .blank: break
            }
        }
    }

    let cx = HUD.pad + map + 28, w = card.width
    let v = shape(fill: s.familyColour.withAlphaComponent(0.16), stroke: s.familyColour, width: 2, radius: 20)
    add(root, v, NSRect(origin: NSPoint(x: cx, y: HUD.pad), size: card))
    add(v, icon(s), NSRect(x: 20, y: 20, width: 48, height: 48))
    add(v, label(s.app.uppercased(), size: 12, weight: .bold, colour: s.familyColour, align: .left),
        NSRect(x: 80, y: 24, width: w - 100, height: 16))
    add(v, label("● \(s.stateLabel) · \(s.age)", size: 13, weight: .semibold, colour: s.stateColour, align: .left),
        NSRect(x: 80, y: 45, width: w - 100, height: 17))
    add(v, label(s.displayTitle, size: 18, weight: .semibold, colour: .white, lines: 2, align: .left),
        NSRect(x: 20, y: 82, width: w - 40, height: 48))
    let tokens = label(s.tokensBurnt ?? "—", size: 22, weight: .bold,
                       colour: NSColor(white: 1, alpha: s.tokensBurnt == nil ? 0.3 : 0.95), align: .left)
    tokens.font = .monospacedDigitSystemFont(ofSize: 22, weight: .bold)
    add(v, tokens, NSRect(x: 20, y: 140, width: w - 40, height: 28))
    add(v, label(s.tokenDetail, size: 12, weight: .medium, colour: NSColor(white: 1, alpha: 0.5), align: .left),
        NSRect(x: 20, y: 170, width: w - 40, height: 16))
    if let hold {
        add(v, holdBar(size: NSSize(width: w - 40, height: 5), colour: s.familyColour, hold: hold),
            NSRect(x: 20, y: card.height - 22, width: w - 40, height: 5))
    }

    add(root, label("Keep holding to open  ·  let go to cancel", size: 12, weight: .medium, colour: dim),
        NSRect(x: 0, y: size.height - HUD.pad - 18, width: size.width, height: 18))
    return root
}

/// Fills left to right over the rest of the hold; Core Animation runs it, the app does no per-frame work.
private func holdBar(size: NSSize, colour: NSColor, hold: Hold) -> NSView {
    let track = NSView(frame: NSRect(origin: .zero, size: size))
    track.wantsLayer = true
    track.layer?.backgroundColor = NSColor(white: 1, alpha: 0.12).cgColor
    track.layer?.cornerRadius = size.height / 2
    track.layer?.masksToBounds = true
    let fill = CALayer()
    fill.backgroundColor = colour.cgColor
    fill.anchorPoint = CGPoint(x: 0, y: 0.5)
    fill.frame = CGRect(origin: .zero, size: size)
    track.layer?.addSublayer(fill)
    guard hold.remaining > 0 else {
        fill.transform = CATransform3DMakeScale(hold.done, 1, 1)
        return track
    }
    let grow = CABasicAnimation(keyPath: "transform.scale.x")
    grow.fromValue = hold.done
    grow.toValue = 1
    grow.duration = hold.remaining
    fill.add(grow, forKey: "hold")
    return track
}

final class SwitcherHUD {
    private var panel: NSPanel?

    func show(_ content: NSView) {
        let panel = self.panel ?? makePanel()
        let screen = NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) } ?? NSScreen.main
        let area = screen?.visibleFrame ?? .zero
        let size = content.frame.size
        panel.contentView = glass(around: content)
        panel.setFrame(NSRect(x: area.midX - size.width / 2, y: area.midY - size.height / 2,
                              width: size.width, height: size.height), display: true)
        panel.alphaValue = 1
        panel.orderFrontRegardless()
    }

    func hide() {
        guard let panel, panel.isVisible else { return }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.15
            panel.animator().alphaValue = 0
        }, completionHandler: { panel.orderOut(nil) })
    }

    private func glass(around content: NSView) -> NSView {
        content.autoresizingMask = [.width, .height]
        if #available(macOS 26.0, *) {
            let g = NSGlassEffectView(frame: content.frame)
            g.cornerRadius = 26
            g.contentView = content
            return g
        }
        let v = NSVisualEffectView(frame: content.frame)
        v.material = .hudWindow
        v.blendingMode = .behindWindow
        v.state = .active
        v.wantsLayer = true
        v.layer?.cornerRadius = 26
        v.layer?.masksToBounds = true
        v.addSubview(content)
        return v
    }

    private func makePanel() -> NSPanel {
        let p = NSPanel(contentRect: .zero, styleMask: [.nonactivatingPanel, .borderless],
                        backing: .buffered, defer: true)
        p.level = .popUpMenu
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.ignoresMouseEvents = true
        p.isReleasedWhenClosed = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        p.appearance = NSAppearance(named: .darkAqua)
        panel = p
        return p
    }
}

/// `Micropad --render out.png [--hold 0.6] [sid-prefix]`: draw the switcher (or, with
/// --hold, the held-key view frozen part-way) from live daemon state to a PNG.
func renderPreview(to path: String) -> Int32 {
    guard let data = try? Data(contentsOf: daemon.appendingPathComponent("state")),
          let snap = try? decodeSnapshot(data) else {
        FileHandle.standardError.write(Data("micropad: daemon not reachable\n".utf8))
        return 1
    }
    let live = snap.keys.filter { $0.sid != nil }
    let recent = Array(snap.others.prefix(6))
    let args = CommandLine.arguments
    let picked = args.last.flatMap { a in (live + recent).first { $0.sid?.hasPrefix(a) == true } }
    let view: NSView
    if let i = args.firstIndex(of: "--hold"), let s = picked ?? live.first {
        view = holdView(s, hold: Hold(done: Double(args[i + 1]) ?? 0.5, remaining: 0), glass: false)
    } else {
        view = padView(keys: live, recent: recent, selected: (picked ?? live.first)?.sid, glass: false)
    }
    guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return 1 }
    view.cacheDisplay(in: view.bounds, to: rep)
    guard let png = rep.representation(using: .png, properties: [:]),
          (try? png.write(to: URL(fileURLWithPath: path))) != nil else { return 1 }
    return 0
}

// MARK: - menu bar

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let hud = SwitcherHUD()
    private var stream: EventStream?
    private var snap: Snapshot?
    private var blink: Timer?
    private var blinkOn = true
    private var holdStart: (sid: String, at: Date)?

    private func hold(for sw: Switcher?) -> Hold? {
        guard let sw, sw.mode == "hold", let sid = sw.sid else {
            holdStart = nil
            return nil
        }
        if holdStart?.sid != sid { holdStart = (sid, Date()) }
        let total = Double(sw.holdMs ?? 1000) / 1000
        let elapsed = min(Date().timeIntervalSince(holdStart!.at), total)
        return Hold(done: elapsed / total, remaining: total - elapsed)
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        render()
        stream = EventStream { [weak self] s in self?.update(s) }
    }

    private var live: [Session] { snap?.keys.filter { $0.sid != nil } ?? [] }

    private func update(_ s: Snapshot?) {
        snap = s
        let waiting = live.contains { $0.state == "waiting" }
        if waiting, blink == nil {
            blink = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
                guard let self else { return }
                self.blinkOn.toggle()
                self.render()
            }
        } else if !waiting {
            blink?.invalidate()
            blink = nil
            blinkOn = true
        }
        render()
        let held = hold(for: s?.switcher)
        if let s, let sw = s.switcher, let sel = sw.sid {
            let all = Dictionary((s.keys + s.others).compactMap { r in r.sid.map { ($0, r) } },
                                 uniquingKeysWith: { a, _ in a })
            if sw.mode == "hold", let session = all[sel] {
                hud.show(holdView(session, hold: held, glass: true))
            } else {
                let recent = (sw.order ?? []).compactMap { all[$0] }.filter { $0.led == nil }
                hud.show(padView(keys: live, recent: recent, selected: sel, glass: true))
            }
        } else {
            holdStart = nil
            hud.hide()
        }
    }

    private func render() {
        guard let button = item.button else { return }
        let out = NSMutableAttributedString()
        let font = NSFont.systemFont(ofSize: 13)
        if snap == nil || live.isEmpty {
            let colour: NSColor = snap == nil ? .systemRed : .tertiaryLabelColor
            out.append(NSAttributedString(string: "◌", attributes: [.foregroundColor: colour, .font: font]))
        }
        for s in live {
            let hidden = s.state == "waiting" && !blinkOn
            let glyph = s.state == "waiting" ? "◆" : (s.state == "working" ? "●" : "○")
            out.append(NSAttributedString(string: glyph, attributes: [
                .foregroundColor: hidden ? NSColor.clear : s.colour, .font: font, .kern: 2,
            ]))
        }
        button.attributedTitle = out
        button.toolTip = snap == nil ? "Micropad daemon not reachable" : "\(live.count) agent sessions"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        guard let snap else {
            menu.addItem(withTitle: "Daemon not reachable (127.0.0.1:48801)", action: nil, keyEquivalent: "")
            addFooter(menu)
            return
        }
        if live.isEmpty { menu.addItem(withTitle: "No live sessions", action: nil, keyEquivalent: "") }
        for s in live { menu.addItem(sessionItem(s)) }
        if !snap.others.isEmpty {
            let recent = NSMenuItem(title: "Recent", action: nil, keyEquivalent: "")
            let sub = NSMenu()
            for s in snap.others.prefix(12) { sub.addItem(sessionItem(s)) }
            recent.submenu = sub
            menu.addItem(.separator())
            menu.addItem(recent)
        }
        if !snap.pad || !snap.warnings.isEmpty {
            menu.addItem(.separator())
            if !snap.pad { menu.addItem(withTitle: "⚠︎ Pad not connected", action: nil, keyEquivalent: "") }
            for w in snap.warnings { menu.addItem(withTitle: "⚠︎ \(w)", action: nil, keyEquivalent: "") }
        }
        addFooter(menu)
    }

    private func addFooter(_ menu: NSMenu) {
        menu.addItem(.separator())
        let dash = NSMenuItem(title: "Open dashboard", action: #selector(openDashboard), keyEquivalent: "d")
        dash.target = self
        menu.addItem(dash)
        let quit = NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)
    }

    private func sessionItem(_ s: Session) -> NSMenuItem {
        let mi = NSMenuItem(title: s.displayTitle, action: #selector(openSession(_:)), keyEquivalent: "")
        mi.target = self
        mi.representedObject = s.sid
        let line = NSMutableAttributedString(string: "● ", attributes: [.foregroundColor: s.colour])
        line.append(NSAttributedString(string: s.displayTitle, attributes: [.font: NSFont.menuFont(ofSize: 0)]))
        let usage = s.tokensBurnt.map { " · " + $0 } ?? ""
        line.append(NSAttributedString(string: "   \(s.app) · \(s.stateLabel) · \(s.age)\(usage)", attributes: [
            .foregroundColor: NSColor.secondaryLabelColor, .font: NSFont.menuFont(ofSize: 11),
        ]))
        mi.attributedTitle = line
        return mi
    }

    @objc private func openSession(_ sender: NSMenuItem) {
        if let sid = sender.representedObject as? String { post("open", ["sid": sid]) }
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(daemon)
    }
}

if let i = CommandLine.arguments.firstIndex(of: "--render"), i + 1 < CommandLine.arguments.count {
    _ = NSApplication.shared
    exit(renderPreview(to: CommandLine.arguments[i + 1]))
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
