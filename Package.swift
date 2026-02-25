// swift-tools-version:5.5
import PackageDescription

let package = Package(
    name: "keypulse",
    platforms: [.macOS(.v12)],
    products: [.executable(name: "keypulse", targets: ["keypulse"])],
    targets: [.executableTarget(name: "keypulse", path: "Sources")])
)
