import Foundation
import CryptoKit
import AppKit
import Darwin

#if !os(macOS) || !arch(arm64)
#error("The native packaging pilot requires arm64 macOS.")
#endif

// The builder replaces these literals before compiling for arm64-apple-macos13.0.
private let engineSHA256 = "@@ENGINE_SHA256@@"
private let engineName = "@@ENGINE_NAME@@"
private let appName = "SOFT Tracking Engine.app"
private let fm = FileManager.default

private enum Failure: String, Error {
    case configuration = "STL_E_CONFIG"
    case wrapper = "STL_E_WRAPPER"
    case arguments = "STL_E_ARGUMENTS"
    case root = "STL_E_ROOT"
    case cache = "STL_E_CACHE"
    case lock = "STL_E_LOCK"
    case archive = "STL_E_ARCHIVE"
    case digest = "STL_E_SHA256"
    case extraction = "STL_E_EXTRACT"
    case layout = "STL_E_LAYOUT"
    case signature = "STL_E_SIGNATURE"
    case payload = "STL_E_PAYLOAD"
    case environment = "STL_E_ENV"
    case execution = "STL_E_EXEC"
    case cleanup = "STL_E_CLEANUP"
    case io = "STL_E_IO"
}

private func metadata(_ path: URL) throws -> stat? {
    var info = stat()
    if lstat(path.path, &info) == 0 { return info }
    if errno == ENOENT { return nil }
    throw Failure.io
}

private func directory(_ path: URL, owned: Bool = false, privateMode: Bool = false) throws {
    guard let info = try metadata(path),
          (info.st_mode & S_IFMT) == S_IFDIR else { throw Failure.cache }
    if owned && (info.st_uid != geteuid() || (info.st_mode & 0o022) != 0) {
        throw Failure.cache
    }
    if privateMode && (info.st_mode & 0o077) != 0 { throw Failure.cache }
}

private func makePrivateDirectory(_ path: URL) throws {
    if mkdir(path.path, 0o700) != 0 && errno != EEXIST { throw Failure.cache }
    try directory(path, owned: true, privateMode: true)
}

private func wrapperURL() throws -> URL {
    var size: UInt32 = 0
    _ = _NSGetExecutablePath(nil, &size)
    guard size > 0 else { throw Failure.wrapper }
    var bytes = [CChar](repeating: 0, count: Int(size))
    guard _NSGetExecutablePath(&bytes, &size) == 0 else { throw Failure.wrapper }
    let path = URL(fileURLWithPath: String(cString: bytes)).standardizedFileURL
    // Canonicalize the directory, keeping the wrapper's role-bearing basename.
    return path.deletingLastPathComponent().resolvingSymlinksInPath()
        .appendingPathComponent(path.lastPathComponent)
}

private func role(for wrapper: URL) throws -> String {
    switch wrapper.lastPathComponent {
    case "soft-tracking-app": return "app"
    case "soft-tracking-host-app": return "native"
    case "soft-tracking": return "bootstrap"
    case "soft-tracking-host": return "bootstrap-native"
    case "SOFT-Tracking-Setup": return "setup"
    default: throw Failure.wrapper
    }
}

private func runTool(_ executable: String, _ arguments: [String], failure: Failure) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    // Helpers must neither consume a native message nor pollute its binary channel.
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do { try process.run() } catch { throw failure }
    process.waitUntilExit()
    guard process.terminationReason == .exit && process.terminationStatus == 0 else {
        throw failure
    }
}

private func verifyDigest(_ archive: URL) throws {
    let fd = open(archive.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard fd >= 0 else { throw Failure.archive }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    defer { try? handle.close() }
    var info = stat()
    guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else {
        throw Failure.archive
    }
    var hasher = SHA256()
    do {
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
    } catch { throw Failure.archive }
    let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
    guard digest == engineSHA256 else { throw Failure.digest }
}

private func verifyEngine(_ cache: URL) throws -> URL {
    try directory(cache, owned: true, privateMode: true)
    let entries = try fm.contentsOfDirectory(atPath: cache.path)
    guard entries == [appName] else { throw Failure.layout }
    let app = cache.appendingPathComponent(appName, isDirectory: true)
    let contents = app.appendingPathComponent("Contents", isDirectory: true)
    let macOS = contents.appendingPathComponent("MacOS", isDirectory: true)
    for path in [app, contents, macOS] { try directory(path, owned: true) }
    let executable = macOS.appendingPathComponent("soft-tracking-engine")
    guard let info = try metadata(executable), (info.st_mode & S_IFMT) == S_IFREG,
          info.st_uid == geteuid(), (info.st_mode & 0o022) == 0,
          access(executable.path, X_OK) == 0 else { throw Failure.layout }
    try runTool("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path], failure: .signature)
    return app
}

private func prepareEngine(archive: URL, parent: URL, cache: URL) throws -> URL {
    try directory(parent, owned: true)
    let lockPath = parent.appendingPathComponent(".engine-\(engineSHA256).lock")
    let fd = open(lockPath.path, O_CREAT | O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
    guard fd >= 0 else { throw Failure.lock }
    // Never unlink a lock file: waiters must continue to lock the same inode.
    defer { _ = close(fd) }
    var info = stat()
    guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
          info.st_uid == geteuid(), info.st_nlink == 1,
          (info.st_mode & 0o077) == 0 else { throw Failure.lock }
    let deadline = ProcessInfo.processInfo.systemUptime + 120
    while flock(fd, LOCK_EX | LOCK_NB) != 0 {
        guard errno == EWOULDBLOCK || errno == EINTR,
              ProcessInfo.processInfo.systemUptime < deadline else { throw Failure.lock }
        Thread.sleep(forTimeInterval: 0.1)
    }
    defer { _ = flock(fd, LOCK_UN) }

    if try metadata(cache) != nil {
        try verifyDigest(archive)
        return try verifyEngine(cache)
    }

    var template = Array(parent.appendingPathComponent(".engine-\(engineSHA256).tmp.XXXXXX").path.utf8CString)
    guard mkdtemp(&template) != nil else { throw Failure.cache }
    let temporary = URL(fileURLWithPath: String(cString: template), isDirectory: true)
    defer {
        do { try fm.removeItem(at: temporary) }
        catch { FileHandle.standardError.write(Data((Failure.cleanup.rawValue + "\n").utf8)) }
    }
    try directory(temporary, owned: true, privateMode: true)
    let copiedArchive = temporary.appendingPathComponent(engineName)
    guard let source = try metadata(archive), (source.st_mode & S_IFMT) == S_IFREG else {
        throw Failure.archive
    }
    do { try fm.copyItem(at: archive, to: copiedArchive) } catch { throw Failure.archive }
    // Hash the private copy that ditto will actually read, not a mutable source path.
    try verifyDigest(copiedArchive)
    let extracted = temporary.appendingPathComponent("extracted", isDirectory: true)
    try makePrivateDirectory(extracted)
    try runTool("/usr/bin/ditto", ["-x", "-k", copiedArchive.path, extracted.path], failure: .extraction)
    _ = try verifyEngine(extracted)
    // Staging is on the same filesystem. The exclusive rename cannot replace any
    // unexpected cache entry (including a dangling symlink) created while extracting.
    guard renamex_np(extracted.path, cache.path, UInt32(RENAME_EXCL)) == 0 else {
        throw Failure.cache
    }
    return cache.appendingPathComponent(appName, isDirectory: true)
}

private func launch(_ app: URL, role: String, wrapper: URL, arguments: [String]) throws -> Never {
    let executable = app.appendingPathComponent("Contents/MacOS/soft-tracking-engine")
    let argv = [executable.path, "--tracking-role", role, "--tracking-wrapper", wrapper.path, "--"] + arguments
    var pointers = argv.map { strdup($0) }
    defer { for pointer in pointers { free(pointer) } }
    guard pointers.allSatisfy({ $0 != nil }) else { throw Failure.execution }
    pointers.append(nil)
    // Replace the wrapper, retaining file descriptors, signals and the engine's exit status.
    _ = pointers.withUnsafeMutableBufferPointer { execv(executable.path, $0.baseAddress!) }
    throw Failure.execution
}

@MainActor
private func report(_ failure: Failure, setup: Bool, arguments: [String]) -> Never {
    FileHandle.standardError.write(Data((failure.rawValue + "\n").utf8))
    let headless = arguments.contains("--health-check") || arguments.contains("--migration-self-test")
        || arguments.contains("--prepare-engine")
    if setup && !headless {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "SOFT Tracking Setup could not start"
        alert.informativeText = "The engine could not be prepared or verified. Diagnostic code: \(failure.rawValue)"
        alert.addButton(withTitle: "OK")
        app.activate(ignoringOtherApps: true)
        alert.runModal()
    }
    exit(1)
}

let originalArguments = Array(CommandLine.arguments.dropFirst())
var isSetup = false
do {
    let wrapper = try wrapperURL()
    let selectedRole = try role(for: wrapper)
    isSetup = selectedRole == "setup"
    guard #available(macOS 13.0, *) else { throw Failure.configuration }
    guard engineSHA256.utf8.count == 64,
          engineSHA256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
          engineName == "engine-\(engineSHA256).zip" else { throw Failure.configuration }
    let prepareOnly = originalArguments.first == "--prepare-engine"
    if prepareOnly && originalArguments.count != 1 { throw Failure.arguments }

    let archive: URL
    let parent: URL
    let cache: URL
    if isSetup {
        guard getuid() != 0 && geteuid() != 0 else { throw Failure.root }
        guard let resources = Bundle.main.resourceURL else { throw Failure.payload }
        let payload = resources.appendingPathComponent("setup-payload", isDirectory: true)
        try directory(payload)
        guard setenv("SOFT_TRACKING_SETUP_PAYLOAD", payload.path, 1) == 0 else {
            throw Failure.environment
        }
        archive = resources.appendingPathComponent(engineName)
        let home = fm.homeDirectoryForCurrentUser.resolvingSymlinksInPath()
        let library = home.appendingPathComponent("Library", isDirectory: true)
        try directory(library, owned: true)
        let caches = library.appendingPathComponent("Caches", isDirectory: true)
        if try metadata(caches) == nil { try makePrivateDirectory(caches) }
        try directory(caches, owned: true)
        parent = caches.appendingPathComponent("SOFTTrackingSetup-engine", isDirectory: true)
        try makePrivateDirectory(parent)
        cache = parent.appendingPathComponent(engineSHA256, isDirectory: true)
    } else {
        guard unsetenv("SOFT_TRACKING_SETUP_PAYLOAD") == 0 else { throw Failure.environment }
        parent = wrapper.deletingLastPathComponent()
        archive = parent.appendingPathComponent(engineName)
        cache = parent.appendingPathComponent(".engine-\(engineSHA256)", isDirectory: true)
    }
    let app = try prepareEngine(archive: archive, parent: parent, cache: cache)
    if prepareOnly {
        let health: [String: Any] = ["ok": true, "role": selectedRole, "engine_sha256": engineSHA256,
                                   "bundle_path": app.path, "wrapper_path": wrapper.path]
        var json = try JSONSerialization.data(withJSONObject: health, options: [.sortedKeys])
        json.append(0x0a)
        try FileHandle.standardOutput.write(contentsOf: json)
    } else {
        try launch(app, role: selectedRole, wrapper: wrapper, arguments: originalArguments)
    }
} catch let failure as Failure {
    MainActor.assumeIsolated {
        report(failure, setup: isSetup, arguments: originalArguments)
    }
} catch {
    MainActor.assumeIsolated {
        report(.io, setup: isSetup, arguments: originalArguments)
    }
}
