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
}

struct Switcher: Decodable { let led: Int? }

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
                    onSnapshot(try JSONDecoder().decode(Snapshot.self, from: Data(line.dropFirst(6).utf8)))
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

enum HUDLayout {
    static let card = NSSize(width: 210, height: 196)
    static let gap: CGFloat = 14
    static let pad: CGFloat = 20
    static let header: CGFloat = 30
}

func label(_ text: String, size: CGFloat, weight: NSFont.Weight, colour: NSColor, lines: Int = 1) -> NSTextField {
    let f = lines > 1 ? NSTextField(wrappingLabelWithString: text) : NSTextField(labelWithString: text)
    f.font = .systemFont(ofSize: size, weight: weight)
    f.textColor = colour
    f.alignment = .center
    f.maximumNumberOfLines = lines
    f.lineBreakMode = lines > 1 ? .byWordWrapping : .byTruncatingTail
    f.cell?.truncatesLastVisibleLine = true
    return f
}

func switcherView(_ sessions: [Session], selected: Int?, glass: Bool) -> NSView {
    let n = CGFloat(max(sessions.count, 1))
    let width = HUDLayout.pad * 2 + n * HUDLayout.card.width + (n - 1) * HUDLayout.gap
    let height = HUDLayout.pad * 2 + HUDLayout.header + HUDLayout.card.height
    let root = Shape(frame: NSRect(x: 0, y: 0, width: width, height: height))
    root.appearance = NSAppearance(named: .darkAqua)
    root.fill = NSColor(white: 0.06, alpha: glass ? 0.55 : 0.96)
    root.stroke = NSColor(white: 1, alpha: 0.10)
    root.lineWidth = 1
    root.radius = 26

    let hint = label("Turn the wheel to choose  ·  stop to open", size: 12, weight: .medium,
                     colour: NSColor(white: 1, alpha: 0.45))
    hint.frame = NSRect(x: 0, y: HUDLayout.pad - 4, width: width, height: 18)
    root.addSubview(hint)

    for (i, s) in sessions.enumerated() {
        let x = HUDLayout.pad + CGFloat(i) * (HUDLayout.card.width + HUDLayout.gap)
        let c = card(s, selected: s.led == selected)
        c.frame.origin = NSPoint(x: x, y: HUDLayout.pad + HUDLayout.header)
        root.addSubview(c)
    }
    return root
}

private func card(_ s: Session, selected: Bool) -> NSView {
    let w = HUDLayout.card.width, h = HUDLayout.card.height
    let v = Shape(frame: NSRect(origin: .zero, size: HUDLayout.card))
    v.radius = 18
    v.fill = selected ? s.familyColour.withAlphaComponent(0.20) : NSColor(white: 1, alpha: 0.05)
    v.stroke = s.familyColour
    v.lineWidth = selected ? 3 : 0

    let icon = NSImageView(frame: NSRect(x: (w - 52) / 2, y: 16, width: 52, height: 52))
    icon.image = s.appIcon
    icon.imageScaling = .scaleProportionallyUpOrDown
    v.addSubview(icon)

    let app = label(s.app.uppercased(), size: 12, weight: .bold, colour: s.familyColour)
    app.frame = NSRect(x: 8, y: 74, width: w - 16, height: 16)
    v.addSubview(app)

    let title = label(s.displayTitle, size: 15, weight: .semibold,
                      colour: NSColor(white: 1, alpha: selected ? 1 : 0.85), lines: 2)
    title.frame = NSRect(x: 12, y: 96, width: w - 24, height: 42)
    v.addSubview(title)

    let state = label("● \(s.stateLabel)  ·  \(s.age)", size: 13, weight: .semibold, colour: s.stateColour)
    state.frame = NSRect(x: 8, y: h - 34, width: w - 16, height: 18)
    v.addSubview(state)
    return v
}

final class SwitcherHUD {
    private var panel: NSPanel?

    func show(_ sessions: [Session], selected: Int) {
        let panel = self.panel ?? makePanel()
        let content = switcherView(sessions, selected: selected, glass: true)
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

/// `Micropad --render out.png`: draw the switcher from live daemon state to a PNG.
func renderPreview(to path: String) -> Int32 {
    guard let data = try? Data(contentsOf: daemon.appendingPathComponent("state")),
          let snap = try? JSONDecoder().decode(Snapshot.self, from: data) else {
        FileHandle.standardError.write(Data("micropad: daemon not reachable\n".utf8))
        return 1
    }
    let live = snap.keys.filter { $0.sid != nil }
    let view = switcherView(live, selected: snap.switcher?.led ?? live.first?.led, glass: false)
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
        if let sel = s?.switcher?.led, !live.isEmpty {
            hud.show(live, selected: sel)
        } else {
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
        line.append(NSAttributedString(string: "   \(s.app) · \(s.stateLabel) · \(s.age)", attributes: [
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
