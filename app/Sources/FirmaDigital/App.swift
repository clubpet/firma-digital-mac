import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var state: AppState?
    var pendingOpen: URL?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { MainActor.assumeIsolated { state?.shutdown() } }
    func application(_ application: NSApplication, open urls: [URL]) {
        guard let u = urls.first else { return }
        MainActor.assumeIsolated { if let state { state.open(u) } else { pendingOpen = u } }
    }
}

@main
struct FirmaDigitalApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var state = AppState()

    var body: some Scene {
        Window("Firma Digital", id: "main") {
            ContentView(state: state)
                .frame(minWidth: 960, minHeight: 620)
                .onAppear {
                    delegate.state = state
                    if let u = delegate.pendingOpen { state.open(u); delegate.pendingOpen = nil }
                    NSApp.activate(ignoringOtherApps: true)
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Abrir PDF…") { state.openPanel() }.keyboardShortcut("o")
            }
            CommandGroup(replacing: .help) {
                Button("Ayuda de Firma Digital") {
                    if let u = Bundle.main.url(forResource: "README", withExtension: "md") {
                        NSWorkspace.shared.open([u], withApplicationAt: URL(fileURLWithPath: "/System/Applications/TextEdit.app"),
                                                configuration: NSWorkspace.OpenConfiguration())
                    }
                }
                Button("Validador oficial de firmas") { NSWorkspace.shared.open(URL(string: "https://validadordefirmas.gob.ar")!) }
            }
        }
    }
}
