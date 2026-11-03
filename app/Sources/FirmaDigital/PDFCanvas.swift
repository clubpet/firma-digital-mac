import AppKit
import PDFKit
import SwiftUI

/// PDFView donde se dibuja / mueve / redimensiona el recuadro de firma. El recuadro se guarda en
/// coordenadas de pagina PDF (puntos, origen abajo-izquierda) via PDFView.convert(_:to:).
final class SignPDFView: PDFView {
    var editable = true { didSet { overlay.needsDisplay = true } }
    var stampLines: [String] = [] { didSet { overlay.needsDisplay = true } }
    private(set) var sigPage: PDFPage?
    private(set) var sigRect: CGRect = .zero
    var onSigChange: ((PDFPage?, CGRect) -> Void)?
    var onDropURL: ((URL) -> Void)?
    var onPageChange: ((Int) -> Void)?
    private var lastPage = -1

    private let overlay = Overlay()
    private enum Drag { case new(CGPoint), move(CGRect, CGPoint), resize(CGRect, Edges) }
    private struct Edges: OptionSet { let rawValue: Int
        static let l = Edges(rawValue: 1), r = Edges(rawValue: 2), b = Edges(rawValue: 4), t = Edges(rawValue: 8) }
    private var drag: Drag?
    private var dragPage: PDFPage?
    private weak var observedClip: NSClipView?

    override init(frame: NSRect) {
        super.init(frame: frame)
        overlay.host = self
        addSubview(overlay)
        registerForDraggedTypes([.fileURL])
        for n in [Notification.Name.PDFViewScaleChanged, .PDFViewPageChanged, .PDFViewDocumentChanged, .PDFViewDisplayModeChanged] {
            NotificationCenter.default.addObserver(self, selector: #selector(refresh), name: n, object: self)
        }
    }
    required init?(coder: NSCoder) { fatalError() }

    func setSignature(page: PDFPage?, rect: CGRect) {
        sigPage = page; sigRect = rect
        overlay.needsDisplay = true
    }

    override var document: PDFDocument? { didSet { lastPage = -1; refresh() } }

    @objc private func refresh() {
        overlay.needsDisplay = true
        if let p = currentPage, let i = document?.index(for: p), i != lastPage {
            lastPage = i
            DispatchQueue.main.async { self.onPageChange?(i) }
        }
    }

    override func layout() {
        super.layout()
        overlay.frame = bounds
        if subviews.last !== overlay { addSubview(overlay, positioned: .above, relativeTo: nil) }
        if observedClip == nil, let clip = Self.find(NSScrollView.self, in: self)?.contentView {
            observedClip = clip
            clip.postsBoundsChangedNotifications = true
            NotificationCenter.default.addObserver(self, selector: #selector(refresh), name: NSView.boundsDidChangeNotification, object: clip)
        }
    }

    private static func find<T: NSView>(_ t: T.Type, in v: NSView) -> T? {
        for s in v.subviews { if let m = s as? T ?? find(t, in: s) { return m } }
        return nil
    }

    /// Recuadro de firma en coordenadas de este view (nil si no hay).
    var sigViewRect: CGRect? {
        guard let p = sigPage, sigRect.width > 0 else { return nil }
        return convert(sigRect, from: p)
    }

    private func edges(at p: CGPoint, in r: CGRect) -> Edges? {
        let k: CGFloat = 7
        guard r.insetBy(dx: -k, dy: -k).contains(p) else { return nil }
        var e: Edges = []
        if abs(p.x - r.minX) < k { e.insert(.l) } else if abs(p.x - r.maxX) < k { e.insert(.r) }
        if abs(p.y - r.minY) < k { e.insert(.b) } else if abs(p.y - r.maxY) < k { e.insert(.t) }
        return e
    }

    override func mouseDown(with event: NSEvent) {
        guard editable, document != nil else { return super.mouseDown(with: event) }
        let p = convert(event.locationInWindow, from: nil)
        if let vr = sigViewRect, let e = edges(at: p, in: vr) {
            drag = e.isEmpty ? .move(vr, p) : .resize(vr, e)
            dragPage = sigPage
        } else if let page = page(for: p, nearest: true) {
            drag = .new(p)
            dragPage = page
        }
    }

    override func mouseDragged(with event: NSEvent) {
        guard let d = drag, let page = dragPage else { return super.mouseDragged(with: event) }
        let p = convert(event.locationInWindow, from: nil)
        var r: CGRect
        switch d {
        case .new(let s):
            r = CGRect(x: min(s.x, p.x), y: min(s.y, p.y), width: abs(p.x - s.x), height: abs(p.y - s.y))
        case .move(let r0, let s):
            r = r0.offsetBy(dx: p.x - s.x, dy: p.y - s.y)
        case .resize(let r0, let e):
            var (x0, x1, y0, y1) = (r0.minX, r0.maxX, r0.minY, r0.maxY)
            let m = 20 * scaleFactor
            if e.contains(.l) { x0 = min(p.x, x1 - m) }
            if e.contains(.r) { x1 = max(p.x, x0 + m) }
            if e.contains(.b) { y0 = min(p.y, y1 - m) }
            if e.contains(.t) { y1 = max(p.y, y0 + m) }
            r = CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0)
        }
        var pr = convert(r, to: page)
        if case .move = d { pr = Self.shift(pr, into: page.bounds(for: .cropBox)) } else { pr = pr.intersection(page.bounds(for: .cropBox)) }
        update(page, pr.isNull ? .zero : pr)
    }

    override func mouseUp(with event: NSEvent) {
        guard let d = drag, let page = dragPage else { return super.mouseUp(with: event) }
        drag = nil
        let up = convert(event.locationInWindow, from: nil)
        if case .new(let s) = d, hypot(up.x - s.x, up.y - s.y) < 4 || sigRect.width * scaleFactor < 12 || sigRect.height * scaleFactor < 12 {
            // Click sin arrastrar: recuadro por defecto 220x60 pt (orientado como se ve en pantalla) centrado en el click.
            let w = 220 * scaleFactor, h = 60 * scaleFactor
            var pr = convert(CGRect(x: s.x - w / 2, y: s.y - h / 2, width: w, height: h), to: page)
            pr = Self.shift(pr, into: page.bounds(for: .cropBox))
            update(page, pr)
        }
        onSigChange?(sigPage, sigRect)
    }

    private func update(_ page: PDFPage, _ r: CGRect) {
        sigPage = page; sigRect = r
        overlay.needsDisplay = true
    }

    /// Mueve r para que quede dentro de box (achica si no entra).
    static func shift(_ r: CGRect, into box: CGRect) -> CGRect {
        var r = r
        r.size.width = min(r.width, box.width); r.size.height = min(r.height, box.height)
        r.origin.x = min(max(r.minX, box.minX), box.maxX - r.width)
        r.origin.y = min(max(r.minY, box.minY), box.maxY - r.height)
        return r
    }

    override func mouseMoved(with event: NSEvent) {
        super.mouseMoved(with: event)
        guard editable, document != nil else { return }
        let p = convert(event.locationInWindow, from: nil)
        guard let vr = sigViewRect, let e = edges(at: p, in: vr) else { return NSCursor.crosshair.set() }
        if e.isEmpty { NSCursor.openHand.set() }
        else if e == .l || e == .r { NSCursor.resizeLeftRight.set() }
        else if e == .t || e == .b { NSCursor.resizeUpDown.set() }
        else { NSCursor.crosshair.set() }
    }

    // MARK: arrastrar un PDF desde Finder
    private func droppedPDF(_ s: NSDraggingInfo) -> URL? {
        (s.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL])?
            .first { $0.pathExtension.lowercased() == "pdf" }
    }
    override func draggingEntered(_ s: NSDraggingInfo) -> NSDragOperation { droppedPDF(s) != nil ? .copy : [] }
    override func draggingUpdated(_ s: NSDraggingInfo) -> NSDragOperation { droppedPDF(s) != nil ? .copy : [] }
    override func performDragOperation(_ s: NSDraggingInfo) -> Bool {
        guard let u = droppedPDF(s) else { return false }
        onDropURL?(u)
        return true
    }

    /// Dibuja el recuadro y la vista previa del sello encima del PDF; no recibe eventos.
    final class Overlay: NSView {
        weak var host: SignPDFView?
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func draw(_ dirty: NSRect) {
            guard let host, let vr0 = host.sigViewRect else { return }
            let r = convert(vr0, from: host)
            let accent = NSColor.controlAccentColor
            accent.withAlphaComponent(0.08).setFill()
            r.fill()
            let border = NSBezierPath(rect: r.insetBy(dx: 0.5, dy: 0.5))
            border.lineWidth = 1.5
            accent.setStroke()
            border.stroke()

            let size = max(5, 9 * host.scaleFactor)
            let para = NSMutableParagraphStyle(); para.lineBreakMode = .byTruncatingTail
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont(name: "Helvetica", size: size) ?? .systemFont(ofSize: size),
                .foregroundColor: NSColor.black.withAlphaComponent(0.85), .paragraphStyle: para,
            ]
            let text = host.stampLines.joined(separator: "\n") as NSString
            NSGraphicsContext.saveGraphicsState()
            NSBezierPath(rect: r).addClip()
            text.draw(in: r.insetBy(dx: 4 * host.scaleFactor, dy: 3 * host.scaleFactor), withAttributes: attrs)
            NSGraphicsContext.restoreGraphicsState()

            guard host.editable else { return }
            accent.setFill()
            for c in [CGPoint(x: r.minX, y: r.minY), CGPoint(x: r.maxX, y: r.minY), CGPoint(x: r.minX, y: r.maxY), CGPoint(x: r.maxX, y: r.maxY)] {
                NSBezierPath(ovalIn: CGRect(x: c.x - 4, y: c.y - 4, width: 8, height: 8)).fill()
            }
        }
        override var isFlipped: Bool { false }
    }
}

struct PDFCanvas: NSViewRepresentable {
    @ObservedObject var state: AppState

    func makeNSView(context: Context) -> SignPDFView {
        let v = SignPDFView()
        v.autoScales = true
        v.displayMode = .singlePageContinuous
        v.displaysPageBreaks = true
        v.backgroundColor = .underPageBackgroundColor
        v.onSigChange = { page, rect in
            state.sigPageIndex = page.flatMap { v.document?.index(for: $0) }
            state.sigRect = rect
        }
        v.onDropURL = { state.open($0) }
        v.onPageChange = { state.currentPage = $0 }
        state.pdfView = v
        return v
    }

    func updateNSView(_ v: SignPDFView, context: Context) {
        if v.document !== state.document { v.document = state.document }
        v.editable = state.mode == .firmar
        v.stampLines = state.stampLines
        let page = state.sigPageIndex.flatMap { state.document?.page(at: $0) }
        if v.sigPage !== page || v.sigRect != state.sigRect { v.setSignature(page: page, rect: state.sigRect) }
    }
}
