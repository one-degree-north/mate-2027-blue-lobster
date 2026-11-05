import Metal
import ModelIO
import os
import RealityKit

private var timeFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "hh:mm:ss SSSS"
    return formatter
}()

private var percentFormatter = {
    let formatter = NumberFormatter()
    formatter.numberStyle = .percent
    formatter.minimumIntegerDigits = 2
    formatter.maximumIntegerDigits = 2
    formatter.minimumFractionDigits = 2
    formatter.maximumFractionDigits = 2

    return formatter
}()

private var secondsFormatter = {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.maximumFractionDigits = 2

    return formatter
}()

private var timeIntervalFormatter = {
    let formatter = DateComponentsFormatter()
    formatter.allowedUnits = [.minute, .second]
    formatter.zeroFormattingBehavior = .pad

    return formatter
}()

var LOG = false

private func info(_ message: String, terminator: String = "\n") {
    if LOG {
        print("\u{001B}[0;37m[\(timeFormatter.string(from: Date()))] \(message)\u{001B}[0;0m", terminator: terminator)
    }
}

private func warn(_ message: String, terminator: String = "\n") {
    print("\u{001B}[0;33m[\(timeFormatter.string(from: Date()))] \(message)\u{001B}[0;0m", terminator: terminator)
}

// The state of the current (or last) reconstruction. Python reads it with get_state().
// 2, 3 and 4 mean "finished", which is what is_completed() reports.
private enum RunState: Int32 {
    case idle = 0
    case running = 1
    case done = 2
    case failed = 3
    case cancelled = 4
}

private struct Status {
    var state: RunState = .idle
    var progress = 0.0
    var eta = 0.0
    var error: String?
}

private let status = OSAllocatedUnfairLock(initialState: Status())
var start_time: Date = .init()
private var activeSession: PhotogrammetrySession?

/// Record a failure for Python to read with get_error(). This replaces the old err(), which
/// printed the message and then exit(1)ed the whole host process. The first error is kept,
/// because later ones are usually consequences of it.
private func fail(_ message: String) {
    print("\u{001B}[0;31m[\(timeFormatter.string(from: Date()))] \u{001B}[1;31m\(message)\u{001B}[0;0m")
    status.withLock { status in
        if status.error == nil {
            status.error = message
        }
        status.state = .failed
    }
}

@_cdecl("set_debug")
public func SetDebug(enabled: Bool) {
    LOG = enabled
}

@_cdecl("is_completed")
public func IsCompleted() -> Bool {
    status.withLock { $0.state.rawValue >= RunState.done.rawValue }
}

@_cdecl("get_state")
public func GetState() -> Int32 {
    status.withLock { $0.state.rawValue }
}

@_cdecl("get_progress")
public func GetProgress() -> Double {
    status.withLock { $0.progress }
}

@_cdecl("get_eta")
public func GetETA() -> Double {
    status.withLock { $0.eta }
}

/// Copy the last error message (UTF-8, NUL-terminated, truncated to fit) into `buffer`.
/// Returns its length in bytes, or 0 if there is no error.
@_cdecl("get_error")
public func GetError(buffer: UnsafeMutablePointer<CChar>, size: Int32) -> Int32 {
    guard size > 0, let message = status.withLock({ $0.error }) else {
        return 0
    }
    let bytes = Array(message.utf8.prefix(Int(size) - 1))
    for (index, byte) in bytes.enumerated() {
        buffer[index] = CChar(bitPattern: byte)
    }
    buffer[bytes.count] = 0
    return Int32(bytes.count)
}

@_cdecl("stop_photogrammetry_session")
public func StopPhotogrammetrySession() {
    guard let session = activeSession, status.withLock({ $0.state == .running }) else {
        warn("No active session to stop.")
        return
    }

    Task {
        session.cancel()
        warn("Photogrammetry session was requested to cancel.")
    }
}

@_cdecl("run_photogrammetry_session")
public func RunPhotogrammetrySession(
    imagesPath: UnsafePointer<CChar>,
    outputPath: UnsafePointer<CChar>,
    tempDir: UnsafePointer<CChar>,
    detailLevel: Int32
) {
    // A fresh run starts from a clean slate: nothing left over from the previous one.
    status.withLock { status in
        status = Status()
        status.state = .running
    }

    let imagesPath = String(cString: imagesPath)
    let outputPath = String(cString: outputPath)
    let tempDir = String(cString: tempDir)

    let detail: PhotogrammetrySession.Request.Detail
    switch detailLevel {
    case 0:
        detail = .preview
    case 1:
        detail = .reduced
    case 2:
        detail = .medium
    case 3:
        detail = .full
    case 4:
        detail = .raw
    default:
        fail("Invalid detail level: \(detailLevel). Must be 0, 1, 2, 3, or 4")
        return
    }

    guard PhotogrammetrySession.isSupported else {
        fail("Photogrammetry is not supported on this device")
        return
    }

    let inputFolderUrl = URL(filePath: imagesPath, directoryHint: URL.DirectoryHint.isDirectory)
    info("Using input images folder: \(String(describing: inputFolderUrl))")

    var configuration = PhotogrammetrySession.Configuration()
    configuration.sampleOrdering = .sequential
    configuration.featureSensitivity = .normal

    info("Using configuration: \(String(describing: configuration))")

    let session: PhotogrammetrySession
    do {
        session = try PhotogrammetrySession(input: inputFolderUrl, configuration: configuration)
        info("Created Photogrammetry Session")
    } catch {
        fail("Could not create the photogrammetry session: \(error.localizedDescription)")
        return
    }

    activeSession = session

    let logger = Task {
        do {
            for try await output in session.outputs {
                switch output {
                case .processingComplete:
                    info("Processing is complete!")
                    info("Time Taken: \(timeIntervalFormatter.string(from: start_time, to: Date()) ?? "")")

                    // An error reported earlier (requestError) already made this run failed.
                    if status.withLock({ $0.state == .failed }) {
                        activeSession = nil
                        return
                    }

                    guard FileManager.default.fileExists(atPath: outputPath) else {
                        fail("Output file was not created - processing failed")
                        activeSession = nil
                        return
                    }

                    // The OBJ export is a side product; losing it must not fail the reconstruction.
                    let modelAsset = MDLAsset(url: URL(filePath: outputPath))
                    modelAsset.loadTextures()

                    let objPath = URL(filePath: tempDir, directoryHint: .isDirectory).appending(component: "model").appending(component: "out.obj")
                    try? FileManager.default.createDirectory(at: objPath.deletingLastPathComponent(), withIntermediateDirectories: true)

                    do {
                        try modelAsset.export(to: objPath)
                    } catch {
                        warn("Could not export the OBJ copy of the model: \(error)")
                    }

                    status.withLock { status in
                        status.progress = 1.0
                        status.eta = 0.0
                        status.state = .done
                    }
                    activeSession = nil
                    return
                case .processingCancelled:
                    warn("Processing was cancelled.")
                    status.withLock { status in
                        status.progress = 0.0
                        status.eta = 0.0
                        status.state = .cancelled
                    }
                    activeSession = nil
                    return
                case let .requestError(request, error):
                    fail("Request \(String(describing: request)) failed: \(error.localizedDescription)")
                case let .requestComplete(request, result):
                    info("Request complete: \(String(describing: request)) with result...")
                    switch result {
                    case let .modelFile(url):
                        info("\tmodelFile available at url=\(url)")
                    default:
                        warn("\tUnexpected result: \(String(describing: result))")
                    }
                case let .requestProgress(_, fractionComplete):
                    status.withLock { $0.progress = fractionComplete }
                    info("Progress = \(percentFormatter.string(from: NSNumber(value: fractionComplete)) ?? "")")
                case .inputComplete:
                    info("Data ingestion is complete.  Beginning processing...")
                case let .invalidSample(id, reason):
                    warn("Invalid Sample! id=\(id)  reason=\"\(reason)\"")
                case let .skippedSample(id):
                    warn("Sample id=\(id) was skipped by processing.")
                case .automaticDownsampling:
                    warn("Automatic downsampling was applied!")
                case let .requestProgressInfo(_, requestInfo):
                    // RealityKit has no estimate early on, so this can be nil.
                    let remaining = requestInfo.estimatedRemainingTime
                    status.withLock { $0.eta = remaining ?? -1 }
                    info("Estimated Time Remaining = \(remaining.flatMap { secondsFormatter.string(from: NSNumber(value: $0)) } ?? "Unknown")")
                case .stitchingIncomplete:
                    warn("Received stitching incomplete message.")
                @unknown default:
                    warn("Output: unhandled message: \(output.localizedDescription)")
                }
            }
        } catch {
            fail("The photogrammetry session stopped unexpectedly: \(error.localizedDescription)")
            activeSession = nil
        }
    }

    withExtendedLifetime((session, logger)) {
        try? FileManager.default.removeItem(atPath: outputPath)
        try? FileManager.default.createDirectory(at: URL(filePath: outputPath).deletingLastPathComponent(), withIntermediateDirectories: true)

        let request = PhotogrammetrySession.Request.modelFile(url: URL(filePath: outputPath), detail: detail)
        info("Using request: \(String(describing: request))")

        start_time = Date()

        do {
            try session.process(requests: [request])
        } catch {
            fail("Could not start processing: \(error.localizedDescription)")
            activeSession = nil
        }
    }
}

@_cdecl("get_bounding_box")
public func GetBoundingBox(
    usdzPath: UnsafePointer<CChar>,
    minX: UnsafeMutablePointer<Float>,
    minY: UnsafeMutablePointer<Float>,
    minZ: UnsafeMutablePointer<Float>,
    maxX: UnsafeMutablePointer<Float>,
    maxY: UnsafeMutablePointer<Float>,
    maxZ: UnsafeMutablePointer<Float>
) {
    let path = String(cString: usdzPath)
    let asset = MDLAsset(url: URL(filePath: path))
    asset.loadTextures()
    let bbox = asset.boundingBox
    minX.pointee = bbox.minBounds.x
    minY.pointee = bbox.minBounds.y
    minZ.pointee = bbox.minBounds.z
    maxX.pointee = bbox.maxBounds.x
    maxY.pointee = bbox.maxBounds.y
    maxZ.pointee = bbox.maxBounds.z
}
