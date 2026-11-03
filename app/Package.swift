// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "FirmaDigital",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "FirmaDigital", path: "Sources/FirmaDigital")]
)
