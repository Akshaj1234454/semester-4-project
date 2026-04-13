# Pothole detector (webcam)

Runs YOLO on a fresh webcam snapshot each time you press Enter.

## Setup

```powershell
Set-Location "c:\Users\aksha\Desktop\DTI proj\phoneCode"
python -m pip install -r requirements.txt
```

Windows-only GPS support (optional):

```powershell
python -m pip install -r requirements-windows.txt
```

## Run

```powershell
python ai.py --device cpu
```

IP camera stream (recommended for Android/Linux containers):

```powershell
python ai.py --device cpu --stream-url "http://<PHONE_IP>:<PORT>/video"
```

- Press `Enter` to capture + predict.
- Type `trip done` to upload the collected `locations` array and quit.
- Output:
  - `YES` = at least one detection
  - `NO` = no detections
- If output is `YES`, the script attempts to read Windows GPS/Location and appends `[latitude, longitude]` into an in-memory nested array.

## Location source (Flask API)

If output is `YES`, the script fetches latitude/longitude from a local Flask endpoint (default: `http://127.0.0.1:5000/location`) and appends `[lat, lon]` to the nested array.

Supported JSON response shapes:
- `{ "latitude": 12.34, "longitude": 56.78 }`
- `{ "lat": 12.34, "lon": 56.78 }`
- `{ "lat": 12.34, "lng": 56.78 }`
- `{ "location": { "latitude": 12.34, "longitude": 56.78 } }`

Override endpoint:

```powershell
python ai.py --location-url "http://127.0.0.1:5000/location"
```

## Upload endpoint

Default endpoint: `http://127.0.0.1:8000/api/clusters/`

Override:

```powershell
python ai.py --api-url "http://127.0.0.1:8000/api/clusters/"
```

If the endpoint is down/unreachable or returns unexpected JSON, the script prints `location_error: ...` but prediction still works.
