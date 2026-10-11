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

    var colour: NSColor {
        switch state {
        case "done": return NSColor(srgbRed: 0.30, green: 0.85, blue: 0.45, alpha: 1)
        case "error": return NSColor(srgbRed: 1.0, green: 0.27, blue: 0.27, alpha: 1)
        default: break
        }
        switch family {
        case "codex": return NSColor(srgbRed: 0.30, green: 0.55, blue: 1.0, alpha: 1)
        case "cursor": return NSColor(srgbRed: 0.65, green: 0.40, blue: 1.0, alpha: 1)
        default: return NSColor(srgbRed: 1.0, green: 0.68, blue: 0.20, alpha: 1)
        }
    }

    var stateLabel: String {
        switch state {
        case "waiting": return "needs you"
        case "working": return "working"
        case "done": return "done"
        case "error": return "error"
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

final class SwitcherHUD {
    private var panel: NSPanel?
    private let stack = NSStackView()

    func show(_ sessions: [Session], selected: Int) {
        let panel = self.panel ?? makePanel()
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for s in sessions { stack.addArrangedSubview(card(s, selected: s.led == selected)) }
        let size = stack.fittingSize
        let frame = NSRect(x: 0, y: 0, width: size.width + 32, height: size.height + 32)
        let screen = NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) } ?? NSScreen.main
        let area = screen?.visibleFrame ?? .zero
        panel.setFrame(NSRect(x: area.midX - frame.width / 2, y: area.midY - frame.height / 2,
                              width: frame.width, height: frame.height), display: true)
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

        let blur = NSVisualEffectView()
        blur.material = .hudWindow
        blur.state = .active
        blur.blendingMode = .behindWindow
        blur.wantsLayer = true
        blur.layer?.cornerRadius = 20
        blur.layer?.masksToBounds = true

        stack.orientation = .horizontal
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        blur.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: blur.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: blur.centerYAnchor),
        ])
        p.contentView = blur
        panel = p
        return p
    }

    private func card(_ s: Session, selected: Bool) -> NSView {
        let v = NSView()
        v.wantsLayer = true
        v.layer?.cornerRadius = 14
        v.layer?.backgroundColor = NSColor.white.withAlphaComponent(selected ? 0.18 : 0.05).cgColor
        v.layer?.borderWidth = selected ? 2 : 0
        v.layer?.borderColor = s.colour.cgColor

        let dot = NSView()
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 9
        dot.layer?.backgroundColor = s.colour.cgColor

        let title = NSTextField(wrappingLabelWithString: s.displayTitle)
        title.font = .systemFont(ofSize: 14, weight: selected ? .semibold : .regular)
        title.textColor = .white
        title.maximumNumberOfLines = 2
        title.lineBreakMode = .byTruncatingTail
        title.alignment = .center

        let sub = NSTextField(labelWithString: "\(s.app) · \(s.stateLabel) · \(s.age)")
        sub.font = .systemFont(ofSize: 11)
        sub.textColor = NSColor.white.withAlphaComponent(0.6)
        sub.alignment = .center

        for x in [dot, title, sub] as [NSView] {
            x.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(x)
        }
        NSLayoutConstraint.activate([
            v.widthAnchor.constraint(equalToConstant: 168),
            v.heightAnchor.constraint(equalToConstant: 120),
            dot.widthAnchor.constraint(equalToConstant: 18),
            dot.heightAnchor.constraint(equalToConstant: 18),
            dot.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            dot.topAnchor.constraint(equalTo: v.topAnchor, constant: 16),
            title.topAnchor.constraint(equalTo: dot.bottomAnchor, constant: 10),
            title.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 10),
            title.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -10),
            sub.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -12),
            sub.centerXAnchor.constraint(equalTo: v.centerXAnchor),
        ])
        return v
    }
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

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
