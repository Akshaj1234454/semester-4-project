import asyncio
from flask import Flask, jsonify
import winsdk.windows.devices.geolocation as geolocation

app = Flask(__name__)

async def get_hardware_location():
    """Taps into the native Windows Geolocation service"""
    locator = geolocation.Geolocator()
    # Requesting the position from the OS
    pos = await locator.get_geoposition_async()
    return pos.coordinate.point.position.latitude, pos.coordinate.point.position.longitude

@app.route('/location', methods=['GET'])
def get_location():
    try:
        # Running the async hardware call
        lat, lon = asyncio.run(get_hardware_location())
        
        # Following your specified format
        return jsonify({
            "latitude": lat,
            "longitude": lon
        })
    except Exception as e:
        # Fallback in case location services are disabled or unavailable
        print(f"Hardware Error: {e}")
        return jsonify({
            "latitude": 0.0, 
            "longitude": 0.0,
            "error": "Ensure Windows Location Services are enabled"
        }), 500

if __name__ == '__main__':
    # host='0.0.0.0' allows the Raspberry Pi to find this PC on the network
    app.run(host='0.0.0.0', port=5000)