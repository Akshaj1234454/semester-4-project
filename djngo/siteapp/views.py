import json
import math
import time
import urllib.parse
import urllib.request
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import LocationPoint


CLUSTER_RADIUS_KM = 0.5  # 500 meters

# Simple in-process cache for Overpass responses (dev-friendly).
# Keyed by rounded lat/lng/radius, expires after TTL seconds.
_ROADS_CACHE = {}
_ROADS_CACHE_TTL_S = 300
_OVERPASS_ENDPOINTS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.nchc.org.tw/api/interpreter',
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def cluster_location_points(points):
    clusters = []
    for p in points:
        lat = float(p.lat)
        lon = float(p.lng)
        pid = int(p.id)

        added = False
        for c in clusters:
            if haversine(lat, lon, c['lat'], c['lng']) <= CLUSTER_RADIUS_KM:
                c['count'] += 1
                c['point_ids'].append(pid)
                added = True
                break
        if not added:
            clusters.append({'lat': lat, 'lng': lon, 'count': 1, 'point_ids': [pid]})
    return clusters


def index(request):
    points = LocationPoint.objects.all().only('id', 'lat', 'lng')
    clusters = cluster_location_points(points)

    context = {
        'clusters_json': json.dumps(clusters)
    }
    return render(request, "siteapp/index.html", context)


@csrf_exempt
def api_clusters(request):
    # CSRF token is not required for API usage via external POST clients.
    # This endpoint expects a JSON body: {"locations": [[lat, lng], ...]}
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST is allowed'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    locations = payload.get('locations') if isinstance(payload, dict) else None
    if not locations or not isinstance(locations, list):
        return JsonResponse({'error': 'Payload must be {"locations": [[lat,lng], ...]}'}, status=400)

    to_create = []
    for point in locations:
        if not (isinstance(point, (list, tuple)) and len(point) == 2):
            continue
        try:
            lat = float(point[0])
            lng = float(point[1])
        except (TypeError, ValueError):
            continue
        to_create.append(LocationPoint(lat=lat, lng=lng))

    if not to_create:
        return JsonResponse({'error': 'No valid [lat,lng] points found in locations'}, status=400)

    LocationPoint.objects.bulk_create(to_create)

    clusters = cluster_location_points(LocationPoint.objects.all().only('id', 'lat', 'lng'))
    sorted_clusters = sorted(clusters, key=lambda x: x['count'], reverse=True)
    return JsonResponse({'clusters': sorted_clusters, 'points_saved': len(to_create)})


@csrf_exempt
def api_delete_cluster(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST is allowed'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    point_ids = payload.get('point_ids') if isinstance(payload, dict) else None
    if not point_ids or not isinstance(point_ids, list):
        return JsonResponse({'error': 'Payload must be {"point_ids": [1,2,3,...]}'}, status=400)

    normalized_ids = []
    for pid in point_ids:
        try:
            normalized_ids.append(int(pid))
        except (TypeError, ValueError):
            continue

    if not normalized_ids:
        return JsonResponse({'error': 'No valid point ids provided'}, status=400)

    deleted_count, _ = LocationPoint.objects.filter(id__in=normalized_ids).delete()
    return JsonResponse({'deleted_points': deleted_count})


def api_geocode(request):
    q = (request.GET.get('q') or '').strip()
    if not q:
        return JsonResponse({'error': 'Missing q'}, status=400)

    params = {
        'q': q,
        'format': 'jsonv2',
        'limit': 1,
    }
    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            # Nominatim requires identifying User-Agent.
            # For production, replace with your app + contact info.
            'User-Agent': 'RoadWatch/1.0 (local dev)',
            'Accept': 'application/json',
        },
        method='GET',
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Geocoding failed'}, status=502)

    if not data:
        return JsonResponse({'error': 'No results'}, status=404)

    item = data[0]
    try:
        lat = float(item.get('lat'))
        lng = float(item.get('lon'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid result'}, status=502)

    return JsonResponse({
        'query': q,
        'lat': lat,
        'lng': lng,
        'display_name': item.get('display_name') or q,
    })


def api_roads(request):
    """Return nearby road geometries for client-side highlighting.

    Query params:
      - lat: float
      - lng: float
      - r: radius meters (default 500)
    """

    try:
        lat = float(request.GET.get('lat'))
        lng = float(request.GET.get('lng'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid lat/lng'}, status=400)

    try:
        radius_m = int(request.GET.get('r') or 500)
    except (TypeError, ValueError):
        radius_m = 500

    # Hard limit so this endpoint stays responsive
    radius_m = max(50, min(radius_m, 2000))

    key = (round(lat, 5), round(lng, 5), radius_m)
    now = time.time()
    cached = _ROADS_CACHE.get(key)
    if cached and (now - cached['ts']) < _ROADS_CACHE_TTL_S:
        return JsonResponse({'ways': cached['ways'], 'cached': True})

    # Overpass query: common road types, within radius.
    query = (
        '[out:json][timeout:25];\n'
        f'(way(around:{radius_m},{lat},{lng})["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service"];);\n'
        'out geom;'
    )

    last_err = None
    for endpoint in _OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=query.encode('utf-8'),
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'Accept': 'application/json',
                'User-Agent': 'RoadWatch/1.0 (local dev)',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode('utf-8'))

            elements = payload.get('elements') if isinstance(payload, dict) else []
            ways = []
            for el in elements or []:
                if not isinstance(el, dict) or el.get('type') != 'way':
                    continue
                geom = el.get('geometry')
                if not isinstance(geom, list) or len(geom) < 2:
                    continue
                coords = []
                for g in geom:
                    if not isinstance(g, dict):
                        continue
                    try:
                        coords.append([float(g['lat']), float(g['lon'])])
                    except (KeyError, TypeError, ValueError):
                        continue
                if len(coords) >= 2:
                    ways.append(coords)

            _ROADS_CACHE[key] = {'ts': now, 'ways': ways}
            return JsonResponse({'ways': ways, 'cached': False})
        except Exception as e:
            last_err = str(e)
            continue

    return JsonResponse({'error': 'Overpass unavailable', 'detail': last_err}, status=503)