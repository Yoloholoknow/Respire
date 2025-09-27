import os
from flask import Flask, request, jsonify
import google.generativeai as genai
import requests
from dotenv import load_dotenv
import json
import traceback

# Load environment variables from a .env file
load_dotenv()

# --- Configuration ---

# Configure the Generative AI model
try:
    gemini_api_key = os.environ.get("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise ValueError("ERROR: GOOGLE_API_KEY environment variable not set.")
    genai.configure(api_key=gemini_api_key)
    llm = genai.GenerativeModel('gemini-2.5-flash')
except (ValueError, Exception) as e:
    print(e)
    llm = None

MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
WAQI_API_TOKEN = os.environ.get("WAQI_API_TOKEN")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---

def get_lat_lng(location_name):
    """Geocodes a location name to latitude and longitude using Google Geocoding API."""
    if not MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY environment variable not set.")
        return None
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location_name}&key={MAPS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            # Round coordinates to reduce precision - Air Quality API works better with less precise coordinates
            lat = round(location["lat"], 4)
            lng = round(location["lng"], 4)
            print(f"Geocoded '{location_name}' to coordinates: {lat}, {lng}")
            return {"lat": lat, "lng": lng}
        else:
            print(f"Geocoding failed for {location_name}: {data['status']}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error geocoding location: {e}")
        return None

def get_air_quality(lat, lng):
    """Fetches air quality data using the WAQI API for given coordinates.

    Returns a unified structure with keys: provider, raw, aqi, dominant_pollutant, pollutants (list).
    Falls back to an estimation if WAQI token is not configured or WAQI returns no data.
    """
    # Try WAQI first
    if WAQI_API_TOKEN:
        try:
            url = f"https://api.waqi.info/feed/geo:{lat};{lng}/?token={WAQI_API_TOKEN}"
            print(f"Querying WAQI for {lat}, {lng}")
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok" and isinstance(data.get("data"), dict):
                d = data["data"]
                overall_aqi = d.get("aqi")
                dominant = d.get("dominentpol")
                pollutants = []
                iaqi = d.get("iaqi", {})
                for code, val in iaqi.items():
                    # WAQI returns {pm25: {v: 12}, o3: {v: 5}, ...}
                    display = code.upper() if code else ""
                    pollutants.append({
                        "code": code,
                        "displayName": display,
                        "concentration": {"value": val.get("v") if isinstance(val, dict) else val, "units": "µg/m³"},
                        "aqi": None
                    })

                unified = {
                    "provider": "waqi",
                    "raw": data,
                    "aqi": overall_aqi,
                    "dominant_pollutant": dominant,
                    "pollutants": pollutants,
                    "city": d.get("city", {}).get("name") if d.get("city") else None,
                    "time": d.get("time")
                }
                return unified
            else:
                print(f"WAQI returned no data for {lat},{lng}: {data.get('status')}")
        except requests.exceptions.RequestException as e:
            print(f"WAQI request error: {e}")

    # If WAQI not configured or failed, fall back to estimation (do not rely on hard-coded hotspots)
    print("Falling back to estimated/local model for air quality (WAQI unavailable)")
    estimated = estimate_pollution_by_location(lat, lng)
    unified = {
        "provider": "estimate",
        "raw": None,
        "aqi": estimated,
        "dominant_pollutant": None,
        "pollutants": [],
        "city": None,
        "time": None
    }
    return unified

def format_air_quality_data(aqi_data):
    """
    Formats the raw AQI data into a structured JSON object.
    """
    aqi_categories = {
        (0, 50): ("Good", "Air quality is considered satisfactory, and air pollution poses little or no risk.", "Enjoy your usual outdoor activities.", "No specific recommendations needed."),
        (51, 100): ("Moderate", "Air quality is acceptable; however, for some pollutants there may be a moderate health concern for a very small number of people who are unusually sensitive to air pollution.", "No need to modify your usual activities unless you are unusually sensitive to a particular pollutant.", "People with respiratory or heart disease, the elderly, and children should consider reducing prolonged or heavy exertion."),
        (101, 150): ("Unhealthy for Sensitive Groups", "Members of sensitive groups may experience health effects. The general public is not likely to be affected.", "Consider making outdoor activities shorter and less intense. Go indoors if you have symptoms.", "People with respiratory or heart disease, the elderly, and children should reduce prolonged or heavy exertion."),
        (151, 200): ("Unhealthy", "Everyone may begin to experience health effects; members of sensitive groups may experience more serious health effects.", "Reduce or reschedule strenuous activities outdoors. Consider moving activities indoors.", "Sensitive groups should avoid all outdoor exertion."),
        (201, 300): ("Very Unhealthy", "Health alert: everyone may experience more serious health effects.", "Avoid all physical activity outdoors.", "Everyone should remain indoors and keep activity levels low."),
        (301, 500): ("Hazardous", "Health warnings of emergency conditions. The entire population is more likely to be affected.", "Remain indoors and keep windows and doors closed. Avoid all physical activity.", "Everyone should remain indoors and keep activity levels low.")
    }

    # Support unified WAQI-style structure created by get_air_quality
    if not aqi_data:
        return {"overview": {}, "recommendations": {}, "pollutants": []}

    if aqi_data.get('provider') == 'waqi':
        overall_aqi = aqi_data.get('aqi')
        dominant_pollutant_code = aqi_data.get('dominant_pollutant')
        pollutants_list = aqi_data.get('pollutants', [])
        # Convert pollutants_list into expected pollutants_data shape
        pollutants_data = []
        for p in pollutants_list:
            pollutants_data.append({
                'code': p.get('code'),
                'displayName': p.get('displayName'),
                'concentration': p.get('concentration'),
                'additionalInfo': {'aqi': p.get('aqi')}
            })
    else:
        # legacy/estimate
        overall_aqi = aqi_data.get('aqi') if isinstance(aqi_data.get('aqi'), (int, float)) else 0
        dominant_pollutant_code = aqi_data.get('dominant_pollutant')
        pollutants_data = aqi_data.get('pollutants', [])

    category, health_summary, general_rec, sensitive_rec = "Unknown", "No data available.", "No specific recommendations.", "No specific recommendations."
    for (lower_bound, upper_bound), (cat, summary, gen, sens) in aqi_categories.items():
        if lower_bound <= overall_aqi <= upper_bound:
            category = cat
            health_summary = summary
            general_rec = gen
            sensitive_rec = sens
            break

    pollutants = []
    for p in pollutants_data:
        conc = p.get('concentration', {}) or {}
        conc_val = conc.get('value') if isinstance(conc, dict) else conc
        conc_units = conc.get('units') if isinstance(conc, dict) else None
        pollutants.append({
            "name": p.get('displayName') or p.get('code'),
            "aqi": (p.get('additionalInfo', {}) or {}).get('aqi') if p.get('additionalInfo') else p.get('aqi'),
            "concentration": f"{conc_val} {conc_units or ''}".strip()
        })
    
    dominant_pollutant_name = "Unknown"
    for p in pollutants_data:
        if p.get('code') == dominant_pollutant_code:
            dominant_pollutant_name = p.get('displayName')
            break

    formatted_data = {
        "overview": {
            "aqi": overall_aqi,
            "category": category,
            "dominant_pollutant": dominant_pollutant_name,
            "dominant_pollutant_description": "This is the pollutant with the highest concentration in the air right now.",
            "health_summary": health_summary
        },
        "recommendations": {
            "general_population": general_rec,
            "sensitive_groups": sensitive_rec
        },
        "pollutants": pollutants
    }
    return formatted_data

# --- API Endpoints ---

@app.route('/api/geocode', methods=['POST'])
def geocode_location():
    """Geocodes a location name to latitude and longitude."""
    data = request.get_json()
    location_name = data.get('location')

    if not location_name:
        return jsonify({"error": "Location name is required"}), 400

    coordinates = get_lat_lng(location_name)

    if not coordinates:
        return jsonify({"error": f"Could not find coordinates for '{location_name}'"}), 404

    return jsonify(coordinates)

@app.route('/api/query', methods=['POST'])
def handle_query():
    """Main endpoint to handle user's natural language queries."""
    if not llm:
        return jsonify({"error": "LLM not configured. Check your GOOGLE_API_KEY."}), 500
    if not MAPS_API_KEY:
        return jsonify({"error": "Google Maps API key not configured."}), 500

    data = request.get_json()
    user_prompt = data.get('prompt')

    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        location_extraction_prompt = f"Extract only the city and country from the following text, in the format 'City, Country'. If a specific city is not mentioned, identify the most likely major city based on the context. Text: '{user_prompt}'"
        location_response = llm.generate_content(location_extraction_prompt)
        location_name = location_response.text.strip()

        if not location_name or "could not" in location_name.lower():
            return jsonify({"error": "Could not identify a location from your query."}), 400

        coordinates = get_lat_lng(location_name)
        if not coordinates:
            return jsonify({"error": f"Could not find coordinates for '{location_name}'"}), 404

        aqi_data = get_air_quality(coordinates['lat'], coordinates['lng'])
        if not aqi_data:
            return jsonify({"error": "Could not retrieve air quality data for the location."}), 500

        explanation_json = format_air_quality_data(aqi_data)

        return jsonify({
            "coordinates": coordinates,
            "explanation": explanation_json,
            "raw_aqi_data": aqi_data
        })

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/forecast', methods=['POST'])
def get_forecast_data():
    """Provides sample forecast data."""
    forecast_data = {
        "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "datasets": [
            {
                "label": "Predicted AQI",
                "data": [58, 62, 55, 65, 70, 68, 72],
                "fill": False,
                "borderColor": 'rgb(75, 192, 192)',
                "tension": 0.1
            }
        ]
    }
    return jsonify(forecast_data)

@app.route('/api/heatmap-data', methods=['POST'])
def get_heatmap_data():
    """Creates a dense global pollution heatmap using estimated and real data."""
    if not MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY not configured for heatmap")
        return jsonify({"error": "Google Maps API key not configured."}), 500
    # Accept viewport bounds in the POST body to return points only within view
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}

    sw = body.get('sw')
    ne = body.get('ne')
    # Allow client to request a maximum number of points (sensible default)
    max_points = int(body.get('max_points', 1500))

    heatmap_points = []

    # If bounds provided, generate grid only within bounds (adaptive step)
    if sw and ne:
        try:
            lat_min = float(sw.get('lat'))
            lng_min = float(sw.get('lng'))
            lat_max = float(ne.get('lat'))
            lng_max = float(ne.get('lng'))

            # normalize in case of reversed coordinates
            if lat_min > lat_max:
                lat_min, lat_max = lat_max, lat_min

            # handle antimeridian crossing for longitude
            crosses_antimeridian = False
            if lng_min <= lng_max:
                lng_span = lng_max - lng_min
            else:
                crosses_antimeridian = True
                lng_span = (180 - lng_min) + (lng_max + 180)

            # Target ~30x30 grid within viewport; clamp steps to 0.25-8 degrees
            target_cells = 30.0
            lat_step = max(0.25, min(8.0, (lat_max - lat_min) / target_cells if (lat_max - lat_min) > 0 else 1.0))
            lng_step = max(0.25, min(8.0, lng_span / target_cells if lng_span > 0 else 1.0))

            # Iterate latitude
            lat = lat_min
            while lat <= lat_max:
                # iterate longitude, taking antimeridian into account
                if not crosses_antimeridian:
                    lng = lng_min
                    while lng <= lng_max:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        if actual_lng > 180:
                            actual_lng -= 360
                        if actual_lng < -180:
                            actual_lng += 360

                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step
                else:
                    # two ranges: lng_min..180 and -180..lng_max
                    lng = lng_min
                    while lng <= 180:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step
                    lng = -180
                    while lng <= lng_max:
                        actual_lat = max(-85, min(85, lat + (hash(f"{lat}_{lng}") % 3 - 1)))
                        actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                        estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                        heatmap_points.append({
                            "lat": actual_lat,
                            "lng": actual_lng,
                            "aqi": estimated_aqi,
                            "weight": estimated_aqi,
                            "estimated": True
                        })
                        lng += lng_step

                lat += lat_step

        except Exception as e:
            print(f"Error generating bounded heatmap: {e}")

        # Add some real data points inside bounds to improve accuracy
        real_data_points = get_limited_real_data()
        # Filter real data by bounds
        def in_bounds(p):
            plat, plng = p.get('lat'), p.get('lng')
            if lat_min <= plat <= lat_max:
                if not crosses_antimeridian:
                    return lng_min <= plng <= lng_max
                else:
                    return plng >= lng_min or plng <= lng_max
            return False

        real_in_bounds = [p for p in real_data_points if in_bounds(p)]
        heatmap_points.extend(real_in_bounds)

    else:
        # No bounds provided: generate a coarse global grid but cap size
        lat_step = 6
        lng_step = 8
        for lat in range(-80, 81, lat_step):
            for lng in range(-180, 181, lng_step):
                actual_lat = lat + (hash(f"{lat}_{lng}") % 3 - 1)
                actual_lng = lng + (hash(f"{lng}_{lat}") % 4 - 2)
                actual_lat = max(-85, min(85, actual_lat))
                actual_lng = max(-180, min(180, actual_lng))
                estimated_aqi = estimate_pollution_by_location(actual_lat, actual_lng)
                heatmap_points.append({
                    "lat": actual_lat,
                    "lng": actual_lng,
                    "aqi": estimated_aqi,
                    "weight": estimated_aqi,
                    "estimated": True
                })

        heatmap_points.extend(get_limited_real_data())

    # Downsample deterministically if too many points
    total = len(heatmap_points)
    if total > max_points:
        sampled = []
        step = float(total) / float(max_points)
        i = 0.0
        while len(sampled) < max_points and int(i) < total:
            sampled.append(heatmap_points[int(i)])
            i += step
        heatmap_points = sampled

    print(f"Returning {len(heatmap_points)} heatmap points (requested max {max_points})")
    return jsonify(heatmap_points)

def estimate_pollution_by_location(lat, lng):
    """Estimate pollution levels based on geographic location and known patterns."""
    
    # Base pollution level (clean air)
    base_pollution = 20
    
    # Ocean areas - very clean
    if is_ocean_area(lat, lng):
        return 15 + (hash(f"{lat}{lng}") % 10)  # 15-25 range
    
    # Polar regions - very clean
    if abs(lat) > 65:
        return 10 + (hash(f"{lat}{lng}") % 8)  # 10-18 range
    
    pollution_hotspots = [
    # China industrial belt
    {"center": (39.9042, 116.4074), "radius": 15, "pollution": 120},  # Beijing
    {"center": (31.2304, 121.4737), "radius": 15, "pollution": 130},  # Shanghai
    {"center": (23.1291, 113.2644), "radius": 12, "pollution": 110},  # Guangzhou
    {"center": (30.5728, 104.0668), "radius": 12, "pollution": 115},  # Chengdu
    {"center": (22.5431, 114.0579), "radius": 12, "pollution": 125},  # Shenzhen

    # India industrial areas
    {"center": (28.6139, 77.2090), "radius": 10, "pollution": 150},  # Delhi
    {"center": (19.0760, 72.8777), "radius": 10, "pollution": 140},  # Mumbai
    {"center": (22.5726, 88.3639), "radius": 10, "pollution": 145},  # Kolkata
    {"center": (13.0827, 80.2707), "radius": 10, "pollution": 135},  # Chennai
    {"center": (12.9716, 77.5946), "radius": 10, "pollution": 130},  # Bengaluru

    # Middle East
    {"center": (29.3759, 47.9774), "radius": 8, "pollution": 100},   # Kuwait City
    {"center": (25.276987, 55.296249), "radius": 8, "pollution": 95}, # Dubai
    {"center": (21.4225, 39.8262), "radius": 8, "pollution": 90},    # Jeddah
    {"center": (31.9686, 99.9018), "radius": 8, "pollution": 85},    # Riyadh
    {"center": (26.8206, 30.8025), "radius": 8, "pollution": 80},    # Cairo

    # Europe industrial
    {"center": (51.1657, 10.4515), "radius": 12, "pollution": 70},   # Germany
    {"center": (48.8566, 2.3522), "radius": 12, "pollution": 65},    # Paris
    {"center": (51.5074, -0.1278), "radius": 12, "pollution": 60},   # London
    {"center": (52.3791, 4.9009), "radius": 12, "pollution": 55},    # Amsterdam
    {"center": (41.9028, 12.4964), "radius": 12, "pollution": 50},   # Rome

    # North America
    {"center": (34.0522, -118.2437), "radius": 8, "pollution": 80},  # Los Angeles
    {"center": (40.7128, -74.0060), "radius": 8, "pollution": 75},   # New York City
    {"center": (41.8781, -87.6298), "radius": 8, "pollution": 70},   # Chicago
    {"center": (29.7604, -95.3698), "radius": 8, "pollution": 65},   # Houston
    {"center": (37.7749, -122.4194), "radius": 8, "pollution": 60},  # San Francisco

    # Other regions
    {"center": (-23.5505, -46.6333), "radius": 8, "pollution": 85},  # São Paulo
    {"center": (35.6762, 139.6503), "radius": 10, "pollution": 75},  # Tokyo
    {"center": (-33.8688, 151.2093), "radius": 8, "pollution": 65},  # Sydney
    {"center": (39.9042, 32.8597), "radius": 8, "pollution": 60},   # Ankara
    {"center": (55.7558, 37.6173), "radius": 8, "pollution": 55},   # Moscow
]

    
    max_pollution = base_pollution
    
    # Check proximity to pollution hotspots
    for hotspot in pollution_hotspots:
        center_lat, center_lng = hotspot["center"]
        distance = ((lat - center_lat) ** 2 + (lng - center_lng) ** 2) ** 0.5
        
        if distance < hotspot["radius"]:
            # Calculate pollution based on distance from center
            influence = max(0, 1 - (distance / hotspot["radius"]))
            pollution_contribution = hotspot["pollution"] * influence
            max_pollution = max(max_pollution, base_pollution + pollution_contribution)
    
    # Add some randomness for natural variation
    variation = (hash(f"{lat}_{lng}_var") % 20) - 10
    final_pollution = max(5, min(200, int(max_pollution + variation)))
    
    return final_pollution

def is_ocean_area(lat, lng):
    """Determine if coordinates are likely over ocean."""
    # Simplified ocean detection based on major landmasses
    
    # Pacific Ocean
    if lng < -120 or lng > 150:
        if not (lat > 50 and lng > -130 and lng < -100):  # Exclude North America west coast
            return True
    
    # Atlantic Ocean
    if lng > -60 and lng < 20 and (lat > 50 or lat < -20):
        return True
    
    # Indian Ocean  
    if lng > 60 and lng < 120 and lat < -10:
        return True
    
    # Arctic Ocean
    if lat > 70:
        return True
    
    # Antarctic Ocean
    if lat < -60:
        return True
        
    return False

def get_limited_real_data():
    """Get a small amount of real API data for key locations."""
    real_points = []
    
    # Key cities where we want real data (limit to 10 to avoid quota issues)
    key_locations = [
        {"lat": 40.7128, "lng": -74.0060},  # NYC
        {"lat": 34.0522, "lng": -118.2437}, # LA
        {"lat": 51.5074, "lng": -0.1278},   # London
        {"lat": 48.8566, "lng": 2.3522},    # Paris
        {"lat": 35.6762, "lng": 139.6503},  # Tokyo
    ]
    
    for location in key_locations:
        try:
            aqi_data = get_air_quality(location["lat"], location["lng"])
            # aqi_data is the unified structure returned by get_air_quality
            if aqi_data and aqi_data.get('aqi') is not None:
                aqi_value = aqi_data.get('aqi') or 0
                try:
                    aqi_value_num = int(aqi_value)
                except Exception:
                    aqi_value_num = 0
                if aqi_value_num > 0:
                    real_points.append({
                        "lat": location["lat"],
                        "lng": location["lng"],
                        "aqi": aqi_value_num,
                        "weight": aqi_value_num,
                        "estimated": False
                    })
        except:
            pass  # Skip if API call fails
    
    return real_points




if __name__ == '__main__':
    app.run(debug=True, port=5000)