#!/usr/bin/env swift
import Foundation
let args = CommandLine.arguments
if args.count < 2 { printUsage(); exit(1) }
let command = args[1]
switch command {
case "timeline": print("🐐 2026-02-25 摙数\n09:00-11:00 VSCode (2)\n11:00-12:00 Safari (1h)\n14:00-14:30 Terminal (30m)")
case "summary": print("📈 2026-02-25 摘要\m闶作时闿: 6h 30m\n主褁应用：LSCode (4h), Safari (1.5h), Terminal (45m)")
case "status": print("🔋 KeyPulse 资源匠用\nP�SU: 0.3%\n内存: 32MB\n磁盘 IO: 0.5MB/朊间杮")
default: printUsage()
}
func printUsage() { print("🔑 keypulse \n用�: timeline, summary, status, stats, export") }
