// swift-tools-version: 5.5
import PackageDescription

let package = Package(
    name: "KeyPulse",
    platforms: [
        .macOS(.v12)
    ],
    products: [
        .executable(
            name: "keypulse",
            targets: ["KeyPulse"]
        )
    ],
    targets: [
        .executableTarget(
            name: "KeyPulse",
            path: "Sources"
        )
    ]
)
