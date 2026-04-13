from __future__ import annotations

from pathlib import Path
from datetime import datetime
import threading
import time



HOME = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = HOME / "pothole_predictions"

# ==== Configurable endpoints (edit these as needed) ====
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_LOCATION_HOST = "100.119.150.83"
DEFAULT_LOCATION_PORT = 5000

DEFAULT_API_URL = f"http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}/api/clusters/"
DEFAULT_DASHBOARD_URL = f"http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}/"
DEFAULT_LOCATION_URL = f"http://{DEFAULT_LOCATION_HOST}:{DEFAULT_LOCATION_PORT}/location"

# Optional: phone/IP camera stream. Leave as None to use the local webcam.
# Example: "http://<PHONE_IP>:<PORT>/video"
DEFAULT_STREAM_URL = "http://10.12.90.25:8080/video"

# A detection is considered a "pothole hit" only if at least one box has
# confidence >= this threshold.
DEFAULT_MIN_HIT_CONF = 0.70


def _default_weights_candidates() -> list[Path]:
    dataset_best = (
        HOME
        
    / "New pothole detection.v2i.yolov11"
        / "runs"
        / "detect"
        / "exp10-new"
        / "yolov8n-c3k2-66"
        / "weights"
        / "best.pt"
    )
    return [
        HOME / "best.pt",
        dataset_best,
    ]


def _resolve_weights_path(weights: str | None) -> Path:
    if weights:
        return Path(weights).expanduser().resolve()

    for candidate in _default_weights_candidates():
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No weights found. Provide --weights, or place your model at 'best.pt' in the project root."
    )


def _collect_images(source: str, max_images: int) -> list[Path]:
    src = Path(source).expanduser()

    if any(ch in source for ch in ["*", "?"]):
        paths = [Path(p) for p in HOME.glob(source)] if not src.is_absolute() else [Path(p) for p in src.parent.glob(src.name)]
    elif src.is_dir():
        paths = list(src.glob("*.jpg")) + list(src.glob("*.jpeg")) + list(src.glob("*.png"))
    else:
        paths = [src]

    images = [p.resolve() for p in paths if p.exists() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    images.sort()
    if max_images > 0:
        images = images[:max_images]
    return images


def _capture_one_frame_from_camera(*, camera_index: int = 0, warmup_frames: int = 10):
    # Import heavy deps only when needed.
    import cv2

    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
    cap = cv2.VideoCapture(int(camera_index), backend)
    try:
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera index {camera_index}. "
                "Close other apps using the camera and try again."
            )

        frame = None
        reads = max(1, int(warmup_frames))
        for _ in range(reads):
            ok, frame = cap.read()
            if not ok:
                frame = None

        if frame is None:
            raise RuntimeError("Failed to read a frame from the camera.")

        return frame
    finally:
        cap.release()


def _capture_one_frame_from_stream(*, stream_url: str, warmup_frames: int = 10):
    # Import heavy deps only when needed.
    import cv2

    cap = cv2.VideoCapture(str(stream_url))
    try:
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open stream URL: {stream_url}. "
                "Check the URL and that the phone/container can reach it."
            )

        frame = None
        reads = max(1, int(warmup_frames))
        for _ in range(reads):
            ok, frame = cap.read()
            if not ok:
                frame = None

        if frame is None:
            raise RuntimeError("Failed to read a frame from the stream.")

        return frame
    finally:
        cap.release()


def _open_video_capture(*, stream_url: str | None, camera_index: int):
    import cv2

    if stream_url:
        cap = cv2.VideoCapture(str(stream_url))
    else:
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
        cap = cv2.VideoCapture(int(camera_index), backend)

    if not cap.isOpened():
        if stream_url:
            raise RuntimeError(
                f"Could not open stream URL: {stream_url}. "
                "Check the URL and that the phone/container can reach it."
            )
        raise RuntimeError(
            f"Could not open camera index {camera_index}. "
            "Close other apps using the camera and try again."
        )

    # Best-effort: reduce buffering (helps with IP camera streams).
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


def _warmup_capture(cap, *, warmup_frames: int) -> None:
    reads = max(1, int(warmup_frames))
    for _ in range(reads):
        cap.read()


def _read_frame(cap):
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("Failed to read a frame from the camera/stream.")
    return frame


def _start_frame_grabber(cap, *, stop_event: threading.Event, max_fps: float = 30.0):
    """Continuously read frames so the main loop can always use the freshest one.

    This is important for some IP camera / MJPEG / RTSP streams where OpenCV may otherwise
    return buffered (stale) frames when polled infrequently.
    """

    lock = threading.Lock()
    latest_frame = {"frame": None, "seq": 0, "t": 0.0}

    def _worker() -> None:
        min_sleep = 0.0
        try:
            if float(max_fps) > 0:
                min_sleep = 1.0 / float(max_fps)
        except Exception:
            min_sleep = 0.0

        while not stop_event.is_set():
            ok, frame = cap.read()
            if ok and frame is not None:
                with lock:
                    latest_frame["frame"] = frame
                    latest_frame["seq"] += 1
                    latest_frame["t"] = time.monotonic()
            else:
                time.sleep(0.1)
                continue

            if min_sleep:
                time.sleep(min_sleep)

    t = threading.Thread(target=_worker, name="frame-grabber", daemon=True)
    t.start()

    def _get_latest_copy():
        with lock:
            frame = latest_frame["frame"]
            seq = int(latest_frame["seq"])
            t_mono = float(latest_frame["t"])
        if frame is None:
            return None, seq, t_mono
        # Copy so inference doesn't race with the grabber overwriting memory.
        return frame.copy(), seq, t_mono

    return t, _get_latest_copy


def _start_enter_listener(stop_event: threading.Event) -> threading.Thread:
    def _worker() -> None:
        try:
            while not stop_event.is_set():
                # Any line (including empty) means Enter was pressed.
                input()
                stop_event.set()
                return
        except EOFError:
            stop_event.set()

    t = threading.Thread(target=_worker, name="enter-listener", daemon=True)
    t.start()
    return t


def _get_location_from_api(*, location_url: str, timeout_s: float = 2.0) -> tuple[float, float]:
    """Fetch current lat/lon from a local Flask API.

    Expected JSON shapes (any of these):
    - {"latitude": 12.34, "longitude": 56.78}
    - {"lat": 12.34, "lon": 56.78}
    - {"lat": 12.34, "lng": 56.78}
    - {"location": {"latitude": ..., "longitude": ...}}
    """
    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise RuntimeError("requests is required for location. Install it with: pip install requests") from e

    resp = requests.get(str(location_url), timeout=float(timeout_s))
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict) and "location" in data and isinstance(data["location"], dict):
        data = data["location"]

    if not isinstance(data, dict):
        raise RuntimeError(f"location api returned non-object JSON: {type(data).__name__}")

    lat_val = data.get("latitude", data.get("lat"))
    lon_val = data.get("longitude", data.get("lon", data.get("lng")))
    if lat_val is None or lon_val is None:
        raise RuntimeError(f"location api missing lat/lon keys: {sorted(data.keys())}")

    return float(lat_val), float(lon_val)


def _has_any_detection(result) -> bool:
    if result is None:
        return False
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return False
    return len(boxes) > 0


def _max_detection_confidence(result) -> float:
    if result is None:
        return 0.0
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return 0.0
    conf = getattr(boxes, "conf", None)
    if conf is None:
        return 0.0
    try:
        # ultralytics uses torch tensors; handle both tensor-like and list-like.
        return float(conf.max().item())
    except Exception:
        try:
            return float(max(conf))
        except Exception:
            return 0.0


def _upload_locations(*, api_url: str, locations: list[list[float]], timeout_s: float = 10.0) -> None:
    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise RuntimeError("requests is required for upload. Install it with: pip install requests") from e

    payload = {"locations": locations}
    resp = requests.post(api_url, json=payload, timeout=float(timeout_s))
    resp.raise_for_status()
    print("Status:", resp.status_code)
    try:
        print("Response:", resp.json())
    except Exception:
        print("Response:", resp.text)


def _run_predict(
    *,
    weights: Path,
    source: str,
    output_dir: Path,
    conf: float,
    min_hit_conf: float,
    device: str,
    max_images: int,
    startup_check: bool,
    api_url: str,
    upload_timeout_s: float,
    location_url: str,
    location_timeout_s: float,
    stream_url: str | None,
    camera_index: int,
) -> None:
    if startup_check:
        print(
            "startup_check_ok",
            {
                "mode": "predict",
                "weights": str(weights),
                "source": source,
                "output_dir": str(output_dir),
            },
        )
        return

    pothole_locations: list[list[float]] = []  # nested array: [[lat, lon], ...]

    # Import heavy deps only when needed.
    from ultralytics import YOLO
    import cv2

    resolved_device = device
    if str(device).lower() == "auto":
        try:
            import torch

            resolved_device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except Exception:
            resolved_device = "cpu"

    model = YOLO(str(weights))
    output_dir.mkdir(parents=True, exist_ok=True)

    if stream_url:
        print(f"Camera source: stream -> {stream_url}")
    else:
        print(f"Camera source: local camera index -> {camera_index}")

    upload_event = threading.Event()
    _start_enter_listener(upload_event)
    shutdown_event = threading.Event()

    capture_interval_s = 3.0
    print(f"Capturing a frame every {capture_interval_s:.0f} seconds...")
    print("Press Enter at any time to upload locations and quit.")
    print("Ctrl+C to quit without uploading.")

    cap = _open_video_capture(stream_url=stream_url, camera_index=int(camera_index))
    try:
        _warmup_capture(cap, warmup_frames=10)

        _grabber_thread, get_latest_frame = _start_frame_grabber(cap, stop_event=shutdown_event, max_fps=30.0)

        next_capture_at = time.monotonic()
        while True:
            if upload_event.is_set():
                try:
                    _upload_locations(
                        api_url=api_url,
                        locations=pothole_locations,
                        timeout_s=float(upload_timeout_s),
                    )
                    print(f"Now open/refresh {DEFAULT_DASHBOARD_URL} in browser")
                finally:
                    return

            now = time.monotonic()
            if now < next_capture_at:
                time.sleep(min(0.1, next_capture_at - now))
                continue
            next_capture_at = now + capture_interval_s

            frame, _seq, _t_mono = get_latest_frame()
            if frame is None:
                # Stream not ready yet.
                continue
            results = model.predict(
                source=frame,
                conf=float(conf),
                save=False,
                device=resolved_device,
                verbose=False,
            )

            first = results[0] if results else None
            max_conf = _max_detection_confidence(first)
            has_pothole = _has_any_detection(first) and (max_conf >= float(min_hit_conf))

            if _has_any_detection(first):
                print(f"max_conf={max_conf:.3f}")
            print("YES" if has_pothole else "NO")

            if has_pothole:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = output_dir / f"camera_{ts}.jpg"
                annotated = first.plot() if first is not None else frame
                ok = cv2.imwrite(str(out_path), annotated)
                if not ok:
                    raise RuntimeError(f"Failed to write output image: {out_path}")
                print(f"saved -> {out_path}")

                try:
                    lat, lon = _get_location_from_api(
                        location_url=location_url,
                        timeout_s=float(location_timeout_s),
                    )
                    pothole_locations.append([lat, lon])
                    print(pothole_locations)
                except Exception as e:
                    print(f"location_error: {e}")

    except KeyboardInterrupt:
        print("\nStopped.")
        if pothole_locations:
            print("pothole_locations:")
            print(pothole_locations)
    finally:
        shutdown_event.set()
        cap.release()

# On Windows, DataLoader spawns child processes so ALL runnable code must be
# inside this guard to prevent a RuntimeError on import.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser("pothole-detector (predict only)")
    parser.add_argument(
        "--weights",
        default=None,
        help="path to YOLO weights (.pt). Default: ./best.pt if present, else the old dataset best.pt path",
    )
    parser.add_argument(
        "--source",
        default="camera",
        help="(kept for compatibility) ignored — inference always uses the laptop camera",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="where annotated images are written",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="API endpoint for uploading locations when you press Enter",
    )
    parser.add_argument(
        "--upload-timeout",
        type=float,
        default=10.0,
        help="upload timeout in seconds",
    )
    parser.add_argument(
        "--location-url",
        default=DEFAULT_LOCATION_URL,
        help="Flask endpoint that returns current latitude/longitude as JSON",
    )
    parser.add_argument(
        "--location-timeout",
        type=float,
        default=2.0,
        help="location API timeout in seconds",
    )
    parser.add_argument("--conf", type=float, default=0.25, help="confidence threshold")
    parser.add_argument(
        "--min-hit-conf",
        type=float,
        default=float(DEFAULT_MIN_HIT_CONF),
        help="minimum confidence to treat a detection as a pothole hit (affects YES/NO, saving, and location)",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="device for inference: cpu, cuda:0, ... (use 'auto' to pick cuda if available)",
    )
    parser.add_argument(
        "--stream-url",
        default=DEFAULT_STREAM_URL,
        help="IP camera stream URL (HTTP/RTSP). If set, OpenCV reads frames from this URL instead of a local camera.",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="local camera index when --stream-url is not set",
    )
    parser.add_argument("--max-images", type=int, default=0, help="limit number of images (0 = no limit)")
    parser.add_argument(
        "--startup-check",
        action="store_true",
        help="validate args/paths and exit without running inference",
    )
    args = parser.parse_args()

    weights_path = _resolve_weights_path(args.weights)
    _run_predict(
        weights=weights_path,
        source=args.source,
        output_dir=Path(args.output_dir).expanduser().resolve(),
        conf=args.conf,
        min_hit_conf=float(args.min_hit_conf),
        device=args.device,
        max_images=int(args.max_images),
        startup_check=bool(args.startup_check),
        api_url=str(args.api_url),
        upload_timeout_s=float(args.upload_timeout),
        location_url=str(args.location_url),
        location_timeout_s=float(args.location_timeout),
        stream_url=(str(args.stream_url) if args.stream_url else None),
        camera_index=int(args.camera_index),
    )

