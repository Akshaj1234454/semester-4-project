import requests

API_URL = "http://127.0.0.1:8000/api/clusters/"

payload = {
    "locations": [
        # cluster A (Delhi near 28.45,77.58)
        [28.4497964, 77.5843135],
        [28.4510000, 77.5851000],
        [28.4478000, 77.5832000],
        [28.4505000, 77.5860000],
        [28.4521000, 77.5845000],

        # cluster B (near Delhi 28.50,77.60)
        [28.5000000, 77.6000000],
        [28.5013000, 77.6010000],
        [28.4988000, 77.5992000],
        [28.5020000, 77.6027000],

        # separate point far (for another cluster)
        [28.4200000, 77.5600000],
        [28.90, 77.4300],
        [28.9002, 77.44000]
    ]
}

if __name__ == "__main__":
    resp = requests.post(API_URL, json=payload, timeout=10)
    resp.raise_for_status()
    print("Status:", resp.status_code)
    print("Response:", resp.json())
    print("Now open/refresh http://127.0.0.1:8000/ in browser")