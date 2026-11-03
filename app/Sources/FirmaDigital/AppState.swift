import AppKit
import PDFKit
import SwiftUI

enum Mode: String, CaseIterable { case firmar = "Firmar", verificar = "Verificar" }
enum EngineState: Equatable { case starting, ready, failed(String) }

@MainActor
final class AppState: ObservableObject {
    let backend = Backend()
    @Published var engine: EngineState = .starting
    @Published var status: Status?

    @Published var mode: Mode = .firmar { didSet { if mode == .verificar, verification == nil, docURL != nil { verifyCurrent() } } }
    @Published var docURL: URL?
    @Published var document: PDFDocument?
    @Published var openError: String?
    @Published var currentPage = 0
    weak var pdfView: SignPDFView?

    @Published var sigPageIndex: Int?
    @Published var sigRect: CGRect = .zero
    @Published var reason = ""
    @Published var location = ""
    @Published var pin = ""
    @Published var certID: String?
    @Published var signing = false
    @Published var signError: String?
    @Published var pinWarning: String?
    @Published var signedURL: URL?

    @Published var verifying = false
    @Published var verification: Verificacion?
    @Published var verifyError: String?

    @Published var driverInstalled = Backend.systemDriverPaths.contains { FileManager.default.fileExists(atPath: $0) }
    @Published var driverMessage: String?

    private var pollTask: Task<Void, Never>?

    init() {
        backend.onExit = { [weak self] msg in
            Task { @MainActor in self?.engine = .failed(msg); self?.status = nil }
        }
        startEngine()
    }

    func startEngine() {
        engine = .starting
        status = nil
        pollTask?.cancel()
        Task {
            do {
                _ = try await backend.start()
                engine = .ready
                pollTask = Task {
                    while !Task.isCancelled {
                        await self.refreshStatus()
                        try? await Task.sleep(for: .seconds(2))
                    }
                }
            } catch {
                engine = .failed(error.localizedDescription)
            }
        }
    }

    func shutdown() { pollTask?.cancel(); backend.stop() }

    private func refreshStatus() async {
        guard engine == .ready, !signing else { return } // no competir por el token mientras se firma
        if let s = try? await backend.status() { status = s }
    }

    // MARK: token

    var token: Status.Token? { status?.tokens.first }
    var cert: Status.Cert? {
        guard let t = token else { return nil }
        return t.certs.first { $0.id == certID } ?? t.certs.first
    }
    var tokenError: String? { status?.pkcs11_error ?? status?.error }

    static func cuil(_ subject: String) -> String? {
        subject.range(of: #"(CUIL|CUIT)\s*\d{11}"#, options: .regularExpression).map { String(subject[$0]) }
    }
    static func fecha(_ iso: String?, hora: Bool = false) -> String? {
        guard let iso else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let d = f.date(from: iso) ?? { f.formatOptions = [.withInternetDateTime]; return f.date(from: iso) }()
        guard let d else { return iso }
        let o = DateFormatter()
        o.locale = Locale(identifier: "es_AR")
        o.dateFormat = hora ? "dd/MM/yyyy HH:mm" : "dd/MM/yyyy"
        return o.string(from: d)
    }

    var stampLines: [String] {
        let o = DateFormatter(); o.dateFormat = "dd/MM/yyyy HH:mm:ss x" // como el sello real: "-03"
        return ["Firmado digitalmente por", cert?.cn ?? "NOMBRE DEL FIRMANTE", o.string(from: Date())]
    }

    // MARK: documento

    func openPanel() {
        let p = NSOpenPanel()
        p.allowedContentTypes = [.pdf]
        p.allowsMultipleSelection = false
        if p.runModal() == .OK, let u = p.url { open(u) }
    }

    func open(_ url: URL) {
        guard let d = PDFDocument(url: url) else {
            openError = "No se pudo abrir \"\(url.lastPathComponent)\". ¿Es un PDF válido?"
            return
        }
        openError = nil
        docURL = url
        document = d
        currentPage = 0
        sigPageIndex = nil; sigRect = .zero
        signError = nil; pinWarning = nil; signedURL = nil
        verification = nil; verifyError = nil
        if mode == .verificar { verifyCurrent() }
    }

    func goTo(page: Int) {
        guard let d = document, let p = d.page(at: min(max(page, 0), d.pageCount - 1)) else { return }
        pdfView?.go(to: p)
    }

    // MARK: firmar

    var canSign: Bool {
        document != nil && sigPageIndex != nil && sigRect.width > 0 && cert != nil && !pin.isEmpty && !signing && engine == .ready
    }

    func sign() {
        guard canSign, let url = docURL, let page = sigPageIndex else { return }
        let pinNow = pin
        pin = "" // nunca queda guardado: se limpia en cada intento
        signing = true; signError = nil; signedURL = nil; verification = nil
        Task {
            defer { signing = false }
            do {
                let data = try Data(contentsOf: url)
                let r = try await backend.sign(pdf: data, name: url.lastPathComponent, pin: pinNow, page: page + 1, rect: sigRect,
                                               reason: reason, location: location, certID: cert?.id)
                pinWarning = nil
                guard let dest = savePanel(suggested: r.filename ?? url.deletingPathExtension().lastPathComponent + "-firmado.pdf", near: url) else {
                    signError = "Firmado, pero no se guardó: cancelaste el guardado. Volvé a firmar para obtener el archivo."
                    return
                }
                try r.pdf.write(to: dest, options: .atomic)
                signedURL = dest
                let signed = PDFDocument(url: dest)
                document = signed
                docURL = dest
                sigPageIndex = nil; sigRect = .zero
                pdfView?.document = signed // ya, para poder quedarnos en la pagina firmada
                goTo(page: page)
                await verify(data: r.pdf, name: dest.lastPathComponent)
            } catch let e as BackendError {
                signError = e.message
                pinWarning = e.pinWarning
            } catch {
                signError = "No se pudo guardar el archivo firmado: \(error.localizedDescription)"
            }
        }
    }

    private func savePanel(suggested: String, near: URL) -> URL? {
        let p = NSSavePanel()
        p.allowedContentTypes = [.pdf]
        p.nameFieldStringValue = suggested
        p.directoryURL = UserDefaults.standard.url(forKey: "lastSaveDir") ?? near.deletingLastPathComponent()
        guard p.runModal() == .OK, let u = p.url else { return nil }
        UserDefaults.standard.set(u.deletingLastPathComponent(), forKey: "lastSaveDir")
        return u
    }

    // MARK: verificar

    func verifyCurrent() {
        guard let url = docURL else { return }
        Task {
            guard let data = try? Data(contentsOf: url) else { verifyError = "No se pudo leer el archivo."; return }
            await verify(data: data, name: url.lastPathComponent)
        }
    }

    private func verify(data: Data, name: String) async {
        verifying = true; verifyError = nil
        defer { verifying = false }
        do { verification = try await backend.verify(pdf: data, name: name) }
        catch { verifyError = error.localizedDescription }
    }

    // MARK: driver

    func installUserDriver() {
        let dest = URL(fileURLWithPath: Backend.systemDriverPaths[0])
        do {
            let fm = FileManager.default
            try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
            try fm.copyItem(at: Backend.bundledDriver, to: dest)
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dest.path)
            driverInstalled = true
            driverMessage = nil
        } catch {
            driverMessage = "No se pudo instalar el driver: \(error.localizedDescription)"
        }
    }

    func installOfficialPkg() {
        guard FileManager.default.fileExists(atPath: Backend.bundledPkg.path) else {
            driverMessage = "El paquete oficial no está incluido en esta copia de la app."
            return
        }
        NSWorkspace.shared.open([Backend.bundledPkg], withApplicationAt: URL(fileURLWithPath: "/System/Library/CoreServices/Installer.app"),
                                configuration: NSWorkspace.OpenConfiguration())
    }
}
