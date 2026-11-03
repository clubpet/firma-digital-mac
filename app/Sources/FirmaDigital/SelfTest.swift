import AppKit
import PDFKit

/// Prueba de desarrollo (solo con FIRMA_SELFTEST=<pdf>, FIRMA_SELFTEST_PIN, FIRMA_SELFTEST_OUT=<dir>; usar con SoftHSM,
/// nunca con el token real). Por cada pagina: arrastra el recuadro con eventos de mouse sinteticos enviados directo al
/// PDFView (no mueven el mouse del sistema), firma, y comprueba que el sello renderizado cae donde se dibujo.
@MainActor
enum SelfTest {
    static func runIfRequested(_ state: AppState) {
        let env = ProcessInfo.processInfo.environment
        guard let pdf = env["FIRMA_SELFTEST"], let pin = env["FIRMA_SELFTEST_PIN"], let out = env["FIRMA_SELFTEST_OUT"] else { return }
        Task {
            var failures = 0
            defer { print("SELFTEST \(failures == 0 ? "OK" : "FALLO (\(failures))")"); NSApp.terminate(nil) }
            for _ in 0..<120 where state.cert == nil { try? await Task.sleep(for: .milliseconds(500)) }
            guard state.cert != nil else { print("sin token"); failures += 1; return }
            let src = URL(fileURLWithPath: pdf)
            let n = PDFDocument(url: src)!.pageCount
            for i in 0..<n {
                state.open(src)
                try? await Task.sleep(for: .milliseconds(300))
                guard let v = state.pdfView, let page = state.document?.page(at: i), let win = v.window else { failures += 1; continue }
                v.autoScales = false
                v.scaleFactor = [0.9, 1.37, 0.73, 1.15, 1.0][i % 5]
                v.go(to: page)
                try? await Task.sleep(for: .milliseconds(300))
                let pv = v.convert(page.bounds(for: .cropBox), from: page) // pagina visible en coords del view
                let click = i == 4 // ultima pagina: click simple -> recuadro por defecto 220x60
                let a = CGPoint(x: pv.minX + pv.width * 0.22, y: pv.minY + pv.height * 0.31)
                let b = click ? a : CGPoint(x: pv.minX + pv.width * 0.71, y: pv.minY + pv.height * 0.44)
                func ev(_ t: NSEvent.EventType, _ p: CGPoint) -> NSEvent {
                    NSEvent.mouseEvent(with: t, location: v.convert(p, to: nil), modifierFlags: [], timestamp: 0,
                                       windowNumber: win.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
                }
                v.mouseDown(with: ev(.leftMouseDown, a))
                if !click { v.mouseDragged(with: ev(.leftMouseDragged, CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2))) }
                v.mouseDragged(with: ev(.leftMouseDragged, b))
                v.mouseUp(with: ev(.leftMouseUp, b))
                guard let drawn = v.sigViewRect, state.sigPageIndex == i else { print("p\(i + 1): no se dibujo"); failures += 1; continue }
                let sent = state.sigRect
                do {
                    let data = try Data(contentsOf: src)
                    let r = try await state.backend.sign(pdf: data, name: "t.pdf", pin: pin, page: i + 1, rect: sent,
                                                         reason: "selftest", location: "", certID: state.cert?.id)
                    let dest = URL(fileURLWithPath: out).appendingPathComponent("selftest-p\(i + 1).pdf")
                    try r.pdf.write(to: dest)
                    let sdoc = PDFDocument(data: r.pdf)!, spage = sdoc.page(at: i)!
                    let annot = spage.annotations.first { $0.widgetFieldType == .signature }?.bounds ?? .null
                    // Sello tal como lo muestra PDFKit: abrir el firmado en la app, capturar la ventana y buscar la tinta.
                    state.open(dest)
                    try? await Task.sleep(for: .milliseconds(300))
                    guard let shown = state.document?.page(at: i) else { failures += 1; continue }
                    v.autoScales = false
                    let cb = shown.bounds(for: .cropBox), rot = shown.rotation % 180 != 0
                    v.scaleFactor = 0.8 * min(v.bounds.width / (rot ? cb.height : cb.width), v.bounds.height / (rot ? cb.width : cb.height))
                    v.go(to: shown)
                    try? await Task.sleep(for: .milliseconds(1500))
                    if state.currentPage != i { print("p\(i + 1): indicador de pagina desfasado (\(state.currentPage + 1))"); failures += 1 }
                    let ink = screenInk(v, page: shown, out: URL(fileURLWithPath: out).appendingPathComponent("screen-p\(i + 1).png"))
                    // Recuadro dibujado, normalizado a la pagina visible (origen arriba-izquierda, 0..1).
                    let top = v.isFlipped ? drawn.minY - pv.minY : pv.maxY - drawn.maxY
                    let want = CGRect(x: (drawn.minX - pv.minX) / pv.width, y: top / pv.height,
                                      width: drawn.width / pv.width, height: drawn.height / pv.height)
                    let pageW = spage.bounds(for: .cropBox).width, tol = 1.5 / Double(pageW) // ~1.5 pt
                    let d = [ink.minX - want.minX, ink.minY - want.minY, ink.maxX - want.maxX, ink.maxY - want.maxY].map { abs($0) }.max()!
                    let annotOK = abs(annot.minX - sent.minX) < 0.01 && abs(annot.minY - sent.minY) < 0.01
                        && abs(annot.width - sent.width) < 0.02 && abs(annot.height - sent.height) < 0.02
                    let ok = d < tol && annotOK
                    if !ok { failures += 1 }
                    print(String(format: "p%d rot=%d crop=%@ escala=%.2f enviado=(%.2f,%.2f,%.2f,%.2f) /Rect=(%.2f,%.2f,%.2f,%.2f) dibujado=%@ sello=%@ desvio=%.2fpt %@",
                                 i + 1, page.rotation, NSStringFromRect(page.bounds(for: .cropBox)), v.scaleFactor,
                                 sent.minX, sent.minY, sent.width, sent.height, annot.minX, annot.minY, annot.width, annot.height,
                                 fmt(want), fmt(ink), d * pageW, ok ? "OK" : "FALLO"))
                } catch {
                    failures += 1
                    print("p\(i + 1): error \(error.localizedDescription)")
                }
            }
        }
    }

    static func fmt(_ r: CGRect) -> String { String(format: "[%.4f %.4f %.4f %.4f]", r.minX, r.minY, r.maxX, r.maxY) }

    /// Captura la ventana con screencapture y devuelve el bbox normalizado (0..1, arriba-izquierda, relativo a la
    /// pagina visible) de los pixeles oscuros dentro de la pagina.
    static func screenInk(_ v: PDFView, page: PDFPage, out: URL) -> CGRect {
        guard let win = v.window else { return .null }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l", "\(win.windowNumber)", out.path]
        try? p.run(); p.waitUntilExit()
        guard let rep = NSImageRep(contentsOf: out) as? NSBitmapImageRep else { return .null }
        let k = CGFloat(rep.pixelsHigh) / win.frame.height
        let pw = v.convert(v.convert(page.bounds(for: .cropBox), from: page), to: nil) // coords de ventana
        let px = CGRect(x: pw.minX * k, y: (win.frame.height - pw.maxY) * k, width: pw.width * k, height: pw.height * k)
        let vis = v.convert(v.bounds, to: nil)
        let visPx = CGRect(x: vis.minX * k, y: (win.frame.height - vis.maxY) * k, width: vis.width * k, height: vis.height * k)
        guard visPx.contains(px) else { print("la pagina no entra entera en la ventana", px, visPx); return .null }
        let area = px.insetBy(dx: 3, dy: 3)
        var (x0, y0, x1, y1) = (Int.max, Int.max, -1, -1)
        for y in Int(area.minY)..<Int(area.maxY) { for x in Int(area.minX)..<Int(area.maxX) {
            // Tinta = cualquier pixel no blanco: borde/texto del sello, o el resaltado azul que PDFKit pinta sobre
            // el campo de firma cuando no dibuja su apariencia (le pasa con CropBox de origen != 0).
            guard let c = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB),
                  min(c.redComponent, c.greenComponent, c.blueComponent) < 0.96 else { continue }
            x0 = min(x0, x); y0 = min(y0, y); x1 = max(x1, x); y1 = max(y1, y)
        } }
        guard x1 >= 0 else { return .null }
        return CGRect(x: (CGFloat(x0) - px.minX) / px.width, y: (CGFloat(y0) - px.minY) / px.height,
                      width: CGFloat(x1 + 1 - x0) / px.width, height: CGFloat(y1 + 1 - y0) / px.height)
    }
}
