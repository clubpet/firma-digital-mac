import AppKit
import SwiftUI

struct ContentView: View {
    @ObservedObject var state: AppState
    @State private var dropTarget = false

    var body: some View {
        VStack(spacing: 0) {
            if !state.driverInstalled && !UserDefaults.standard.bool(forKey: "driverBannerHidden") { DriverBanner(state: state) }
            HSplitView {
                preview.frame(minWidth: 520, maxWidth: .infinity, maxHeight: .infinity)
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if case .failed(let msg) = state.engine { EngineCard(state: state, message: msg) }
                        if state.mode == .firmar { SignPanel(state: state) } else { VerifyPanel(state: state) }
                    }
                    .padding(20)
                }
                .frame(minWidth: 330, idealWidth: 360, maxWidth: 440)
                .background(.background)
            }
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button { state.openPanel() } label: { Label("Abrir PDF", systemImage: "doc.badge.plus") }
                    .help("Abrir un PDF (Cmd+O)")
            }
            ToolbarItem(placement: .principal) {
                Picker("Modo", selection: $state.mode) {
                    ForEach(Mode.allCases, id: \.self) { Text($0.rawValue) }
                }
                .pickerStyle(.segmented).frame(width: 220)
            }
            ToolbarItemGroup(placement: .primaryAction) {
                if let d = state.document, d.pageCount > 0 {
                    Button { state.goTo(page: state.currentPage - 1) } label: { Label("Anterior", systemImage: "chevron.up") }
                        .disabled(state.currentPage == 0)
                    Text("Página \(state.currentPage + 1) de \(d.pageCount)").monospacedDigit().foregroundStyle(.secondary)
                    Button { state.goTo(page: state.currentPage + 1) } label: { Label("Siguiente", systemImage: "chevron.down") }
                        .disabled(state.currentPage >= d.pageCount - 1)
                }
            }
        }
        .navigationTitle(state.docURL?.lastPathComponent ?? "Firma Digital")
        .onDrop(of: [.fileURL], isTargeted: $dropTarget) { providers in
            _ = providers.first?.loadObject(ofClass: URL.self) { url, _ in
                if let url, url.pathExtension.lowercased() == "pdf" { Task { @MainActor in state.open(url) } }
            }
            return true
        }
    }

    @ViewBuilder private var preview: some View {
        if state.document != nil {
            PDFCanvas(state: state)
        } else {
            VStack(spacing: 14) {
                Image(systemName: "doc.viewfinder").font(.system(size: 52, weight: .light)).foregroundStyle(.secondary)
                Text("Arrastrá un PDF acá").font(.title2.weight(.medium))
                Text(state.mode == .firmar ? "o abrilo con el botón de la barra (Cmd+O) para firmarlo."
                                           : "o abrilo con el botón de la barra (Cmd+O) para verificar sus firmas.")
                    .foregroundStyle(.secondary)
                Button("Abrir PDF…") { state.openPanel() }.controlSize(.large)
                if let e = state.openError { Text(e).foregroundStyle(.red) }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(dropTarget ? Color.accentColor.opacity(0.08) : Color(nsColor: .underPageBackgroundColor))
        }
    }
}

// MARK: - Tarjetas

struct Card<Content: View>: View {
    var tint: Color = .secondary
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) { content }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(tint.opacity(0.25)))
    }
}

struct DriverBanner: View {
    @ObservedObject var state: AppState
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "info.circle").foregroundStyle(.secondary)
            Text(state.driverMessage ?? "Driver del token no instalado en el sistema (necesario solo para Adobe/Firefox).")
                .font(.callout)
            Spacer()
            Button("Instalar en mi usuario") { state.installUserDriver() }
            Button("Instalar paquete oficial…") { state.installOfficialPkg() }
            Button { UserDefaults.standard.set(true, forKey: "driverBannerHidden"); state.objectWillChange.send() } label: {
                Image(systemName: "xmark")
            }.buttonStyle(.borderless).help("Ocultar")
        }
        .controlSize(.small)
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
    }
}

struct EngineCard: View {
    @ObservedObject var state: AppState
    let message: String
    var body: some View {
        Card(tint: .red) {
            Label("El motor de firma no está funcionando", systemImage: "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(.red)
            Text(message).font(.callout)
            Button("Reiniciar motor de firma") { state.startEngine() }
        }
    }
}

struct TokenCard: View {
    @ObservedObject var state: AppState
    var body: some View {
        if state.engine == .starting || (state.engine == .ready && state.status == nil) {
            Card { HStack { ProgressView().controlSize(.small); Text("Iniciando el motor de firma…") } }
        } else if let c = state.cert, let t = state.token {
            Card(tint: .green) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "key.horizontal.fill").font(.title2).foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Token conectado").font(.caption).foregroundStyle(.secondary)
                        Text(c.cn).font(.headline)
                        Text([AppState.cuil(c.subject), AppState.fecha(c.valid_until).map { "vence \($0)" }].compactMap { $0 }.joined(separator: " · "))
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
                if t.certs.count > 1 {
                    Picker("Certificado", selection: Binding(get: { state.cert?.id }, set: { state.certID = $0 })) {
                        ForEach(t.certs, id: \.id) { Text("\($0.cn) (\($0.label))").tag(Optional($0.id)) }
                    }
                }
            }
        } else if state.engine == .ready {
            Card(tint: .orange) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "key.horizontal").font(.title2).foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(state.token == nil ? "Conectá el token" : "El token no tiene certificados").font(.headline)
                        Text(state.tokenError.map { "No se pudo leer el token. \($0)" } ?? "Enchufá el token USB; se detecta solo.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

struct SignPanel: View {
    @ObservedObject var state: AppState
    var body: some View {
        TokenCard(state: state)

        VStack(alignment: .leading, spacing: 6) {
            Text("Ubicación de la firma").font(.headline)
            Text(state.document == nil ? "Abrí un PDF para empezar."
                 : state.sigPageIndex == nil ? "Hacé clic o arrastrá sobre la página para marcar dónde va la firma."
                 : "Firma en la página \(state.sigPageIndex! + 1). Arrastrala para moverla o tirá de los bordes para cambiar el tamaño.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }

        Form {
            TextField("Motivo", text: $state.reason, prompt: Text("Opcional"))
            TextField("Lugar", text: $state.location, prompt: Text("Opcional"))
            SecureField("PIN", text: $state.pin, prompt: Text("PIN del token"))
                .onSubmit { state.sign() }
        }
        .formStyle(.columns)

        Label("Tres PIN incorrectos seguidos bloquean el token.", systemImage: "exclamationmark.shield")
            .font(.caption).foregroundStyle(.secondary)

        if let w = state.pinWarning {
            Label(Int(w).map { "Quedan \($0) intentos de PIN antes de que el token se bloquee." } ?? "Atención: \(w).",
                  systemImage: "exclamationmark.octagon.fill")
                .font(.callout.weight(.semibold)).foregroundStyle(.red)
        }

        Button { state.sign() } label: {
            HStack {
                if state.signing { ProgressView().controlSize(.small) }
                Text(state.signing ? "Firmando…" : "Firmar").frame(maxWidth: .infinity)
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.borderedProminent).controlSize(.large)
        .disabled(!state.canSign)
        .keyboardShortcut(.defaultAction)

        if let e = state.signError {
            Card(tint: .red) { Label(e, systemImage: "xmark.octagon.fill").foregroundStyle(.red) }
        }

        if let u = state.signedURL {
            Card(tint: .green) {
                Label("Documento firmado", systemImage: "checkmark.seal.fill").font(.headline).foregroundStyle(.green)
                Text(u.lastPathComponent).font(.callout)
                Button("Mostrar en Finder") { NSWorkspace.shared.activateFileViewerSelecting([u]) }
            }
            VerifyResults(state: state)
        }
    }
}

struct VerifyPanel: View {
    @ObservedObject var state: AppState
    var body: some View {
        Text("Verificar firmas").font(.title3.weight(.semibold))
        if state.docURL == nil {
            Text("Abrí o arrastrá un PDF firmado para ver quién lo firmó y si fue modificado.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        VerifyResults(state: state)
        Button { NSWorkspace.shared.open(URL(string: "https://validadordefirmas.gob.ar")!) } label: {
            Label("Validador oficial", systemImage: "arrow.up.right.square")
        }
        Text("El validador oficial del Gobierno es el que vale ante terceros. Esta verificación aplica el mismo criterio de forma local.")
            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }
}

struct VerifyResults: View {
    @ObservedObject var state: AppState
    var body: some View {
        if state.verifying {
            HStack { ProgressView().controlSize(.small); Text("Verificando firmas…") }
        } else if let e = state.verifyError {
            Card(tint: .red) { Label(e, systemImage: "xmark.octagon.fill").foregroundStyle(.red) }
        } else if let v = state.verification {
            if v.firmas.isEmpty {
                Card { Label("Este PDF no tiene firmas digitales.", systemImage: "doc") }
            }
            ForEach(v.firmas) { SignatureRow(f: $0) }
        }
    }
}

struct SignatureRow: View {
    let f: Firma

    private var verdict: (String, Color, String) {
        if f.error != nil { return ("No se pudo validar esta firma: el archivo está dañado o usa un formato no soportado.", .red, "xmark.octagon.fill") }
        if f.integra != true { return ("Firma inválida: el documento fue alterado después de firmarse.", .red, "xmark.octagon.fill") }
        if f.sin_modificar != true { return ("Firma válida, pero el documento tuvo cambios después de esta firma.", .orange, "exclamationmark.triangle.fill") }
        if f.cadena_confiable != true { return ("Documento íntegro, pero el certificado no proviene de una autoridad certificante reconocida.", .orange, "exclamationmark.triangle.fill") }
        return ("Firma válida: documento íntegro y firmante reconocido por la PKI argentina.", .green, "checkmark.seal.fill")
    }

    var body: some View {
        let (text, color, icon) = verdict
        Card(tint: color) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: icon).foregroundStyle(color).font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text(f.firmante ?? f.campo).font(.headline)
                    if let s = f.serial_number, !s.isEmpty { Text(s).font(.callout).foregroundStyle(.secondary) }
                }
            }
            Text(text).font(.callout.weight(.medium)).foregroundStyle(color).fixedSize(horizontal: false, vertical: true)
            if f.error == nil {
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 4) {
                    row("Fecha", AppState.fecha(f.fecha_firma, hora: true) ?? "Sin fecha")
                    row("Íntegro", f.integra == true ? "Sí" : "No", ok: f.integra == true)
                    row("Sin modificar", f.sin_modificar == true ? "Sí" : "No", ok: f.sin_modificar == true)
                    row("Cadena confiable", f.cadena_confiable == true ? "Sí (\(f.emisor ?? ""))" : "No (\(f.emisor ?? "emisor desconocido"))",
                        ok: f.cadena_confiable == true)
                    row("Sello de tiempo", f.sello_de_tiempo == true ? "Sí" : "No")
                }
                .font(.callout)
            }
        }
    }

    private func row(_ k: String, _ v: String, ok: Bool? = nil) -> some View {
        GridRow {
            Text(k).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                if let ok { Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .red) }
                Text(v).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
