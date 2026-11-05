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

private func err(_ message: String, terminator: String = "\n") {
    print("\u{001B}[0;31m[\(timeFormatter.string(from: Date()))] \u{001B}[1;31m\(message)\u{001B}[0;0m", terminator: terminator)
    exit(1)
}

private let completed = OSAllocatedUnfairLock(initialState: false)
private let progress = OSAllocatedUnfairLock(initialState: Double(0))
private let eta = OSAllocatedUnfairLock(initialState: Double(0))
var start_time: Date = .init()
private var activeSession: PhotogrammetrySession?

@_cdecl("set_debug")
public func SetDebug(enabled: Bool) {
    LOG = enabled
}

@_cdecl("is_completed")
public func IsCompleted() -> Bool {
    completed.withLock { completed in completed }
}

@_cdecl("get_progress")
public func GetProgress() -> Double {
    progress.withLock { progress in progress }
}

@_cdecl("get_eta")
public func GetETA() -> Double {
    eta.withLock { eta in eta }
}

@_cdecl("stop_photogrammetry_session")
public func StopPhotogrammetrySession() {
    guard let session = activeSession else {
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
        err("Invalid detail level: \(detailLevel). Must be 0, 1, 2, 3, or 4")
        return
    }

    guard PhotogrammetrySession.isSupported else {
        err("Photogrammetry not supported on this device")
        exit(1)
    }

    let inputFolderUrl = URL(filePath: imagesPath, directoryHint: URL.DirectoryHint.isDirectory)
    info("Using input images folder: \(String(describing: inputFolderUrl))")

    var configuration = PhotogrammetrySession.Configuration()
    configuration.sampleOrdering = .sequential
    configuration.featureSensitivity = .normal

    info("Using configuration: \(String(describing: configuration))")

    var possibleSession: PhotogrammetrySession? = nil
    do {
        possibleSession = try PhotogrammetrySession(input: inputFolderUrl, configuration: configuration)
        info("Created Photogrammetry Session")
    } catch {
        err("Error creating session: \(String(describing: error))")
        exit(1)
    }

    guard let session = possibleSession else {
        exit(1)
    }

    activeSession = session

    let logger = Task {
        for try await output in session.outputs {
            switch output {
            case .processingComplete:
                info("Processing is complete!")
                info("Time Taken: \(timeIntervalFormatter.string(from: start_time, to: Date()) ?? "")")

                guard FileManager.default.fileExists(atPath: outputPath) else {
                    warn("Output file was not created - processing may have failed")
                    completed.withLock { completed in completed = true }
                    return
                }

                let modelAsset = MDLAsset(url: URL(filePath: outputPath))
                modelAsset.loadTextures()

                let objPath = URL(filePath: tempDir, directoryHint: .isDirectory).appending(component: "model").appending(component: "out.obj")
                try? FileManager.default.createDirectory(at: objPath.deletingLastPathComponent(), withIntermediateDirectories: true)

                do {
                    try modelAsset.export(to: objPath)
                } catch {
                    err("Error while exporting: \(error)")
                }

                completed.withLock { completed in completed = true }

                return
            case .processingCancelled:
                warn("Processing was cancelled.")
                progress.withLock { $0 = 0.0 }
                eta.withLock { $0 = 0.0 }
                completed.withLock { $0 = false }
                activeSession = nil
                return
            case let .requestError(request, error):
                info("Request \(String(describing: request)) had an error: \(String(describing: error))")
            case let .requestComplete(request, result):
                info("Request complete: \(String(describing: request)) with result...")
                switch result {
                case let .modelFile(url):
                    info("\tmodelFile available at url=\(url)")
                default:
                    warn("\tUnexpected result: \(String(describing: result))")
                }
            case let .requestProgress(_, fractionComplete):
                progress.withLock { progress in progress = fractionComplete }
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
                eta.withLock { eta in eta = requestInfo.estimatedRemainingTime ?? -1 }
                info("Estimated Time Remaining = \(secondsFormatter.string(from: NSNumber(value: requestInfo.estimatedRemainingTime!)) ?? "Unknown")")
            case .stitchingIncomplete:
                warn("Received stitching incomplete message.")
            @unknown default:
                err("Output: unhandled message: \(output.localizedDescription)")
            }
        }
    }

    withExtendedLifetime((session, logger)) {
        do {
            try? FileManager.default.removeItem(atPath: outputPath)
            try? FileManager.default.createDirectory(at: URL(filePath: outputPath).deletingLastPathComponent(), withIntermediateDirectories: true)

            let request = PhotogrammetrySession.Request.modelFile(url: URL(filePath: outputPath), detail: detail)
            info("Using request: \(String(describing: request))")

            start_time = Date()

            try session.process(requests: [request])
        } catch {
            print("Error occured: \(error)")
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
