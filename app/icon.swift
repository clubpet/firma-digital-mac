// Genera el iconset de la app: squircle azul con el simbolo "signature". Uso: swift icon.swift <dir.iconset>
import AppKit

let out = CommandLine.arguments[1]
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px), inset = s * 0.1
    let r = CGRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let path = NSBezierPath(roundedRect: r, xRadius: r.width * 0.225, yRadius: r.width * 0.225)
    NSGradient(starting: NSColor(red: 0.20, green: 0.45, blue: 0.95, alpha: 1), ending: NSColor(red: 0.08, green: 0.22, blue: 0.62, alpha: 1))!
        .draw(in: path, angle: -90)
    let cfg = NSImage.SymbolConfiguration(pointSize: s * 0.42, weight: .medium).applying(.init(paletteColors: [.white]))
    if let sym = NSImage(systemSymbolName: "signature", accessibilityDescription: nil)?.withSymbolConfiguration(cfg) {
        let sz = sym.size
        sym.draw(in: CGRect(x: (s - sz.width) / 2, y: (s - sz.height) / 2, width: sz.width, height: sz.height))
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}
for base in [16, 32, 128, 256, 512] {
    try! render(base).write(to: URL(fileURLWithPath: "\(out)/icon_\(base)x\(base).png"))
    try! render(base * 2).write(to: URL(fileURLWithPath: "\(out)/icon_\(base)x\(base)@2x.png"))
}
