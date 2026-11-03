import Foundation

// MARK: - API types (contrato de firma-backend)

struct Status: Decodable {
    struct Cert: Decodable, Hashable { let label: String; let id: String; let cn: String; let subject: String; let valid_until: String }
    struct Token: Decodable { let slot: Int; let label: String; let serial: String; let certs: [Cert] }
    let lib: String
    let lib_found: Bool
    let trust_roots: Int
    let tokens: [Token]
    let pkcs11_error: String?
    let error: String?
}

struct Firma: Decodable, Identifiable {
    var id: String { campo }
    let campo: String
    let firmante: String?
    let serial_number: String?
    let emisor: String?
    let fecha_firma: String?
    let integra: Bool?
    let sin_modificar: Bool?
    let cobertura: String?
    let modificaciones: String?
    let cadena_confiable: Bool?
    let motivo_confianza: String?
    let sello_de_tiempo: Bool?
    let validado_a: String?
    let resumen: String?
    let error: String?
}

struct Verificacion: Decodable { let firmas: [Firma]; let raices_confianza: Int }

struct SignResult { let pdf: Data; let filename: String?; let level: String?; let signer: String? }

struct BackendError: LocalizedError {
    let message: String
    var pinWarning: String? = nil
    var errorDescription: String? { message }
}

// MARK: - Proceso + cliente HTTP

/// Lanza el motor de firma (binario empaquetado o firma.py en desarrollo) y habla HTTP con el en 127.0.0.1.
final class Backend: @unchecked Sendable {
    private var process: Process?
    private(set) var port: Int?
    private let secret = UUID().uuidString + UUID().uuidString
    private var stopping = false
    var onExit: ((String) -> Void)?

    static let systemDriverPaths = [
        NSHomeDirectory() + "/Library/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib",
        "/opt/CryptoIDE/lib/liblm_cryptoide_pkcs11.dylib",
    ]
    static var res: URL { Bundle.main.resourceURL ?? URL(fileURLWithPath: ".") }
    static var bundledDriver: URL { res.appendingPathComponent("driver/liblm_cryptoide_pkcs11.dylib") }
    static var bundledPkg: URL { res.appendingPathComponent("driver/MSCryptoID-FIPS140-3_Mac_Spanish_V2.0.25.0912.pkg") }

    /// Ejecutable + args previos. Prioridad: env FIRMA_BACKEND > UserDefaults backendPath > bundle.
    /// Si termina en .py se corre con el .venv/bin/python que esta al lado.
    private func command() -> (URL, [String], URL)? {
        let env = ProcessInfo.processInfo.environment
        let fm = FileManager.default
        var path = env["FIRMA_BACKEND"] ?? UserDefaults.standard.string(forKey: "backendPath")
        if path == nil {
            let b = Self.res.appendingPathComponent("backend/firma-backend")
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: b.path, isDirectory: &isDir) {
                path = isDir.boolValue ? b.appendingPathComponent("firma-backend").path : b.path // onedir o onefile
            }
        }
        guard let p = path, fm.fileExists(atPath: p) else { return nil }
        let url = URL(fileURLWithPath: p)
        let dir = url.deletingLastPathComponent()
        if p.hasSuffix(".py") {
            return (dir.appendingPathComponent(".venv/bin/python"), [p], dir)
        }
        return (url, [], dir)
    }

    func start() async throws -> Int {
        stop()
        guard let (exe, pre, dir) = command() else {
            throw BackendError(message: "No se encontró el motor de firma dentro de la app.")
        }
        let env = ProcessInfo.processInfo.environment
        let fm = FileManager.default
        let lib = env["FIRMA_PKCS11_LIB"]
            ?? (fm.fileExists(atPath: Self.bundledDriver.path) ? Self.bundledDriver.path
                : Self.systemDriverPaths.first { fm.fileExists(atPath: $0) } ?? Self.bundledDriver.path)
        let bundledTrust = Self.res.appendingPathComponent("trust")
        let trust = env["FIRMA_TRUST_DIR"]
            ?? (fm.fileExists(atPath: bundledTrust.path) ? bundledTrust.path : dir.appendingPathComponent("trust").path)

        let p = Process()
        p.executableURL = exe
        p.arguments = pre + ["--port", "0", "--lib", lib, "--trust", trust, "--token", secret]
        p.currentDirectoryURL = dir
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.standardError // ponytail: logs del motor a la consola, sin captura
        p.terminationHandler = { [weak self] proc in
            guard let self, !self.stopping else { return }
            self.port = nil
            self.onExit?("El motor de firma se detuvo (código \(proc.terminationStatus)).")
        }
        stopping = false
        try p.run()
        process = p

        let port: Int = try await withCheckedThrowingContinuation { cont in
            var buf = Data()
            var done = false
            let finish: (Result<Int, Error>) -> Void = { r in
                guard !done else { return }
                done = true
                out.fileHandleForReading.readabilityHandler = nil
                cont.resume(with: r)
            }
            let q = DispatchQueue(label: "backend.ready")
            out.fileHandleForReading.readabilityHandler = { h in
                let chunk = h.availableData
                q.sync {
                    if chunk.isEmpty { return finish(.failure(BackendError(message: "El motor de firma no arrancó."))) }
                    buf.append(chunk)
                    let s = String(decoding: buf, as: UTF8.self)
                    if let r = s.range(of: #"READY port=(\d+)"#, options: .regularExpression),
                       let n = Int(s[r].split(separator: "=")[1]) {
                        finish(.success(n))
                    }
                }
            }
            q.asyncAfter(deadline: .now() + 90) { finish(.failure(BackendError(message: "El motor de firma tardó demasiado en arrancar."))) }
        }
        // Despues de READY seguimos drenando stdout para que el pipe nunca se llene.
        out.fileHandleForReading.readabilityHandler = { h in _ = h.availableData }
        self.port = port
        return port
    }

    func stop() {
        guard let p = process else { return }
        stopping = true
        if let port {
            var r = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/shutdown")!, timeoutInterval: 1)
            r.setValue(secret, forHTTPHeaderField: "X-Firma-Token")
            let sem = DispatchSemaphore(value: 0)
            URLSession.shared.dataTask(with: r) { _, _, _ in sem.signal() }.resume()
            _ = sem.wait(timeout: .now() + 1)
        }
        if p.isRunning { p.terminate() }
        process = nil
        port = nil
    }

    // MARK: HTTP

    private func request(_ path: String, method: String = "GET", form: [(String, Any)]? = nil, timeout: TimeInterval = 120) throws -> URLRequest {
        guard let port else { throw BackendError(message: "El motor de firma no está corriendo.") }
        var r = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!, timeoutInterval: timeout)
        r.httpMethod = method
        r.setValue(secret, forHTTPHeaderField: "X-Firma-Token")
        if let form {
            let boundary = "firma-\(UUID().uuidString)"
            r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            var body = Data()
            for (name, value) in form {
                body.append("--\(boundary)\r\n".data(using: .utf8)!)
                if let (filename, data) = value as? (String, Data) {
                    let safe = filename.replacingOccurrences(of: "\"", with: "")
                    body.append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(safe)\"\r\nContent-Type: application/pdf\r\n\r\n".data(using: .utf8)!)
                    body.append(data)
                } else {
                    body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)".data(using: .utf8)!)
                }
                body.append("\r\n".data(using: .utf8)!)
            }
            body.append("--\(boundary)--\r\n".data(using: .utf8)!)
            r.httpBody = body
        }
        return r
    }

    private func send(_ r: URLRequest) async throws -> (Data, HTTPURLResponse) {
        do {
            let (d, resp) = try await URLSession.shared.data(for: r)
            return (d, resp as! HTTPURLResponse)
        } catch {
            throw BackendError(message: "No se pudo comunicar con el motor de firma.")
        }
    }

    private static func errorText(_ d: Data) -> String {
        if let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let e = j["error"] as? String { return e }
        let s = String(decoding: d, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty || s.hasPrefix("{") || s.hasPrefix("<") ? "Ocurrió un error inesperado." : s
    }

    func status() async throws -> Status {
        let (d, resp) = try await send(try request("/status", timeout: 10))
        guard resp.statusCode == 200 else { throw BackendError(message: Self.errorText(d)) }
        return try JSONDecoder().decode(Status.self, from: d)
    }

    func verify(pdf: Data, name: String) async throws -> Verificacion {
        let (d, resp) = try await send(try request("/verify", method: "POST", form: [("pdf", (name, pdf))]))
        guard resp.statusCode == 200 else { throw BackendError(message: Self.errorText(d)) }
        do { return try JSONDecoder().decode(Verificacion.self, from: d) }
        catch { throw BackendError(message: "La respuesta de verificación no se pudo leer.") }
    }

    func sign(pdf: Data, name: String, pin: String, page: Int, rect: CGRect, reason: String, location: String, certID: String?) async throws -> SignResult {
        // ponytail: puntos enteros (1 pt = 0,35 mm), el backend original parsea int.
        var form: [(String, Any)] = [
            ("pdf", (name, pdf)), ("pin", pin), ("page", page),
            ("x", Int(rect.minX.rounded())), ("y", Int(rect.minY.rounded())),
            ("w", Int(rect.width.rounded())), ("h", Int(rect.height.rounded())),
            ("reason", reason), ("location", location),
        ]
        if let certID { form.append(("cert_id", certID)) }
        let (d, resp) = try await send(try request("/sign", method: "POST", form: form, timeout: 300))
        let h = { (k: String) in (resp.value(forHTTPHeaderField: k)).map { $0.removingPercentEncoding ?? $0 } }
        guard resp.statusCode == 200 else {
            throw BackendError(message: Self.errorText(d), pinWarning: h("X-Pin-Attempts-Left"))
        }
        return SignResult(pdf: d, filename: h("X-Filename"), level: h("X-Level"), signer: h("X-Signer"))
    }
}
